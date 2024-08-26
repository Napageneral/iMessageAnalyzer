import os
import sqlite3
import shutil
import json
from collections import defaultdict
from datetime import datetime
import re
from fuzzywuzzy import fuzz
import sys
import logging

def get_bundle_dir():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

def get_output_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def analyze_manifest_db(manifest_path):
    if not os.path.exists(manifest_path):
        return f"Manifest.db not found at {manifest_path}"

    try:
        conn = sqlite3.connect(manifest_path)
        cursor = conn.cursor()

        # Get all table names
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        
        result = f"Tables in Manifest.db: {[table[0] for table in tables]}\n\n"

        # For each table, get its structure and a sample of data
        for table in tables:
            table_name = table[0]
            result += f"Table: {table_name}\n"
            
            # Get table structure
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = cursor.fetchall()
            result += f"Columns: {[col[1] for col in columns]}\n"
            
            # Get a sample of data
            cursor.execute(f"SELECT * FROM {table_name} LIMIT 5")
            sample_data = cursor.fetchall()
            result += f"Sample data: {sample_data}\n\n"

        conn.close()
        return result

    except sqlite3.Error as e:
        return f"SQLite error in analyze_manifest_db: {e}"
    except Exception as e:
        return f"Unexpected error in analyze_manifest_db: {e}"


def get_file_paths(manifest_path):
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest.db not found at {manifest_path}")

    try:
        conn = sqlite3.connect(manifest_path)
        cursor = conn.cursor()

        # Check for different possible table names
        table_names = ['Files', 'files', 'File', 'file']
        found_table = None

        for table in table_names:
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
            if cursor.fetchone():
                found_table = table
                break

        if not found_table:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            available_tables = ', '.join([t[0] for t in cursor.fetchall()])
            raise Exception(f"No suitable table found in Manifest.db. Available tables: {available_tables}")

        # Use the found table name
        cursor.execute(f"SELECT fileID, relativePath FROM {found_table} WHERE domain='HomeDomain'")
        files = cursor.fetchall()
        conn.close()

        result = {relativePath: fileID for fileID, relativePath in files if relativePath}
        print(f"Found {len(result)} files in Manifest.db")
        return result

    except sqlite3.Error as e:
        raise Exception(f"SQLite error in get_file_paths: {e}")
    except Exception as e:
        raise Exception(f"Unexpected error in get_file_paths: {e}")

def copy_relevant_files(backup_folder, output_dir, file_paths):
    copied_files = []
    for relative_path, file_id in file_paths.items():
        if 'sms.db' in relative_path.lower() or 'addressbook.sqlitedb' in relative_path.lower():
            source = os.path.join(backup_folder, file_id[:2], file_id)
            destination = os.path.join(output_dir, os.path.basename(relative_path))
            shutil.copy2(source, destination)
            copied_files.append(destination)
    return copied_files

def normalize_phone_number(phone):
    if phone is None:
        return "Unknown"
    # Remove all non-digit characters
    digits = re.sub(r'\D', '', str(phone))
    
    # If it's a US number (11 digits starting with 1), remove the leading 1
    if len(digits) == 11 and digits.startswith('1'):
        return digits[1:]
    return digits

def get_contacts(address_book_path):
    conn = sqlite3.connect(address_book_path)
    cursor = conn.cursor()
    
    # Check the table structure
    cursor.execute("PRAGMA table_info(ABPerson)")
    columns = [column[1] for column in cursor.fetchall()]
    
    # Adjust the query based on available columns
    query = """
    SELECT ABPerson.ROWID, ABPerson.First, ABPerson.Last, ABMultiValue.value
    """
    
    if 'ImageData' in columns:
        query += ", ABPerson.ImageData"
    elif 'ThumbnailData' in columns:
        query += ", ABPerson.ThumbnailData"
    else:
        query += ", NULL as ImageData"
    
    query += """
    FROM ABPerson
    LEFT JOIN ABMultiValue ON ABPerson.ROWID = ABMultiValue.record_id
    WHERE ABMultiValue.property IN (3, 4)  -- 3 for phone, 4 for email
    """
    
    cursor.execute(query)
    contacts = cursor.fetchall()
    conn.close()
    
    contact_dict = {}
    for row_id, first, last, value, image_data in contacts:
        full_name = f"{first} {last}".strip()
        if not full_name:
            full_name = "Unknown"
        
        if '@' in value:  # It's an email
            key = value.lower()
        else:  # It's a phone number
            key = normalize_phone_number(value)
        
        if key not in contact_dict or (image_data and not contact_dict[key]['image_data']):
            contact_dict[key] = {
                'name': full_name,
                'image_data': image_data
            }
    
    return contact_dict

def clean_contact_name(name):
    parts = name.split()
    return ' '.join([part for part in parts if part.lower() != 'none'])

def analyze_group_chats_basic(sms_db_path, contacts):
    conn = sqlite3.connect(sms_db_path)
    cursor = conn.cursor()
    
    # Fetch all group chats and their messages
    cursor.execute("""
        SELECT 
            c.ROWID as chat_id,
            c.chat_identifier,
            c.display_name,
            m.ROWID as message_id,
            m.date,
            h.id as participant_id
        FROM 
            chat c
        JOIN 
            chat_message_join cmj ON c.ROWID = cmj.chat_id
        JOIN 
            message m ON cmj.message_id = m.ROWID
        LEFT JOIN 
            handle h ON m.handle_id = h.ROWID
        WHERE 
            c.chat_identifier LIKE 'chat%'
        ORDER BY 
            c.ROWID, m.date
    """)
    
    all_data = cursor.fetchall()
    conn.close()

    group_chats = defaultdict(lambda: {
        'chat_id': None,
        'chat_identifier': None,
        'display_name': None,
        'participants': set(),
        'total_messages': 0,
        'first_message': None,
        'last_message': None
    })

    for row in all_data:
        chat_id, chat_identifier, display_name, message_id, date, participant_id = row
        
        if group_chats[chat_id]['chat_id'] is None:
            group_chats[chat_id]['chat_id'] = chat_id
            group_chats[chat_id]['chat_identifier'] = chat_identifier
            group_chats[chat_id]['display_name'] = display_name

        group_chats[chat_id]['participants'].add(participant_id)
        group_chats[chat_id]['total_messages'] += 1
        
        if group_chats[chat_id]['first_message'] is None or date < group_chats[chat_id]['first_message']:
            group_chats[chat_id]['first_message'] = date
        
        if group_chats[chat_id]['last_message'] is None or date > group_chats[chat_id]['last_message']:
            group_chats[chat_id]['last_message'] = date

    formatted_group_chats = []
    for chat in group_chats.values():
        chat_name = chat['display_name'] if chat['display_name'] else f"Group Chat {chat['chat_identifier']}"
        
        # Match participants to contact names
        matched_participants = []
        for participant in chat['participants']:
            if participant:
                normalized_participant = normalize_phone_number(participant)
                contact_info = contacts.get(normalized_participant, {'name': participant})
                cleaned_name = clean_contact_name(contact_info['name'])
                matched_participants.append(cleaned_name if cleaned_name else participant)
        
        formatted_group_chats.append({
            "chat_id": chat['chat_id'],
            "chat_identifier": chat['chat_identifier'],
            "chat_name": chat_name,
            "participants": matched_participants,
            "total_messages": chat['total_messages'],
            "first_message": format_date(chat['first_message']),
            "last_message": format_date(chat['last_message'])
        })

    return sorted(formatted_group_chats, key=lambda x: x['total_messages'], reverse=True)

TAPBACK_MAPPING = {
    0: "No Tapback",
    2000: "❤️ Heart",
    2001: "👍 Thumbs Up",
    2002: "👎 Thumbs Down",
    2003: "😂 Laugh",
    2004: "!! Exclamation",
    3003: "❓ Question"
}

def clean_guid(guid):
    if guid and '/' in guid:
        return guid.split('/')[-1]
    return guid

def analyze_single_group_chat(sms_db_path, chat_identifier, contacts):
    conn = sqlite3.connect(sms_db_path)
    cursor = conn.cursor()

    logging.debug(f"Analyzing group chat: {chat_identifier}")

    # Fetch all messages for the chat
    cursor.execute("""
        SELECT 
            m.ROWID,
            m.guid,
            m.text,
            m.handle_id,
            m.is_from_me,
            m.associated_message_guid,
            m.associated_message_type,
            m.date,
            h.id as sender_id
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        WHERE m.cache_roomnames = ?
        ORDER BY m.date ASC
    """, (chat_identifier,))
    
    messages = cursor.fetchall()
    conn.close()

    # Initialize counters
    participant_stats = defaultdict(lambda: {
        'message_count': 0,
        'tapbacks_sent': defaultdict(int),
        'tapbacks_received': defaultdict(int)
    })
    message_to_sender = {}

    # First pass: Count messages and store message_guid to sender mapping
    for row in messages:
        rowid, guid, text, handle_id, is_from_me, associated_message_guid, associated_message_type, date, sender_id = row
        
        sender = 'user' if is_from_me else (sender_id or 'unknown')
        participant_stats[sender]['message_count'] += 1
        message_to_sender[clean_guid(guid)] = sender

    # Second pass: Count tapbacks
    for row in messages:
        rowid, guid, text, handle_id, is_from_me, associated_message_guid, associated_message_type, date, sender_id = row
        
        sender = 'user' if is_from_me else (sender_id or 'unknown')

        if associated_message_type:
            # This is a Tapback
            participant_stats[sender]['tapbacks_sent'][associated_message_type] += 1
            cleaned_associated_guid = clean_guid(associated_message_guid)
            if cleaned_associated_guid in message_to_sender:
                original_sender = message_to_sender[cleaned_associated_guid]
                participant_stats[original_sender]['tapbacks_received'][associated_message_type] += 1
            else:
                logging.warning(f"Could not find original message for tapback. GUID: {cleaned_associated_guid}")

    # Match participants to contact names and compile stats
    participant_details = []
    for participant, stats in participant_stats.items():
        if participant == 'user':
            participant_name = "You"
        elif participant == 'unknown':
            participant_name = "Unknown Participant"
        else:
            normalized_participant = normalize_phone_number(participant)
            contact_info = contacts.get(normalized_participant, {'name': participant})
            cleaned_name = clean_contact_name(contact_info['name'])
            participant_name = cleaned_name if cleaned_name else participant
        
        total_tapbacks_sent = sum(stats['tapbacks_sent'].values())
        total_tapbacks_received = sum(stats['tapbacks_received'].values())
        
        participant_details.append({
            'name': participant_name,
            'message_count': stats['message_count'],
            'tapbacks_sent': {TAPBACK_MAPPING.get(t, f"Unknown ({t})"): c for t, c in stats['tapbacks_sent'].items()},
            'tapbacks_received': {TAPBACK_MAPPING.get(t, f"Unknown ({t})"): c for t, c in stats['tapbacks_received'].items()},
            'total_tapbacks_sent': total_tapbacks_sent,
            'total_tapbacks_received': total_tapbacks_received
        })

    logging.debug(f"Participant details: {participant_details}")
    return participant_details

def analyze_imessage_data(sms_db_path):
    conn = sqlite3.connect(sms_db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
    SELECT handle.id, 
           SUM(CASE WHEN message.is_from_me = 1 THEN 1 ELSE 0 END) as sent_count,
           SUM(CASE WHEN message.is_from_me = 0 THEN 1 ELSE 0 END) as received_count,
           MIN(message.date) as first_message,
           MAX(message.date) as last_message
    FROM message 
    JOIN handle ON message.handle_id = handle.ROWID 
    GROUP BY handle.id
    ORDER BY (sent_count + received_count) DESC
    """)
    
    conversations = cursor.fetchall()
    conn.close()
    
    print(f"Sample of first 5 conversations from database: {conversations[:5]}")
    print(f"Types of first conversation data: {[type(item) for item in conversations[0]]}")
    
    return conversations

def format_date(timestamp):
    if timestamp is None:
        return "N/A"
    try:
        date = datetime.fromtimestamp(timestamp / 1e9 + 978307200)
        return date.strftime('%B %d, %Y')
    except (TypeError, ValueError):
        return "Invalid Date"

def get_attachments(sms_db_path):
    conn = sqlite3.connect(sms_db_path)
    cursor = conn.cursor()
    
    query = """
    SELECT 
        message.handle_id,
        attachment.filename,
        attachment.mime_type,
        attachment.total_bytes
    FROM 
        message
    JOIN 
        message_attachment_join ON message.ROWID = message_attachment_join.message_id
    JOIN 
        attachment ON message_attachment_join.attachment_id = attachment.ROWID
    """
    
    cursor.execute(query)
    attachments = cursor.fetchall()
    
    conn.close()
    print(f"Retrieved {len(attachments)} attachments")
    print(f"Sample of first 5 attachments: {attachments[:5]}")
    return attachments

def analyze_image_attachments(sms_db_path):
    conn = sqlite3.connect(sms_db_path)
    cursor = conn.cursor()
    
    query = """
    SELECT 
        handle.id AS phone_number,
        message.is_from_me,
        attachment.mime_type,
        attachment.total_bytes
    FROM 
        message
    JOIN message_attachment_join 
        ON message.ROWID = message_attachment_join.message_id
    JOIN attachment 
        ON message_attachment_join.attachment_id = attachment.ROWID
    JOIN handle 
        ON message.handle_id = handle.ROWID
    WHERE 
        attachment.mime_type LIKE 'image/%'
    """
    
    cursor.execute(query)
    results = cursor.fetchall()
    conn.close()

    image_stats = defaultdict(lambda: {'sent': 0, 'received': 0, 'total_size': 0})
    
    for phone_number, is_from_me, mime_type, total_bytes in results:
        if is_from_me:
            image_stats[phone_number]['sent'] += 1
        else:
            image_stats[phone_number]['received'] += 1
        image_stats[phone_number]['total_size'] += total_bytes

    print(f"Processed {len(results)} images")
    print(f"Image stats for first 5 handles: {dict(list(image_stats.items())[:5])}")
    return image_stats

def get_all_conversations(conversations, contacts, image_stats):
    all_conversations = []
    for identifier, sent_count, received_count, first_message, last_message in conversations:
        contact_info = contacts.get(normalize_phone_number(identifier), {'name': 'Unknown', 'image_data': None})
        contact_name = contact_info['name']
        
        first_message_date = format_date(first_message)
        last_message_date = format_date(last_message)
        
        # Calculate average messages per day
        first_date = datetime.fromtimestamp(first_message / 1e9 + 978307200)
        last_date = datetime.fromtimestamp(last_message / 1e9 + 978307200)
        days_diff = (last_date - first_date).days + 1  # Add 1 to include both first and last day
        total_messages = sent_count + received_count
        avg_messages_per_day = total_messages / days_diff if days_diff > 0 else 0

        # Get image attachment stats
        conversation_image_stats = image_stats.get(identifier, {'sent': 0, 'received': 0, 'total_size': 0})
        
        all_conversations.append({
            "contact_name": contact_name,
            "sent_count": sent_count,
            "received_count": received_count,
            "first_message_date": first_message_date,
            "last_message_date": last_message_date,
            "avg_messages_per_day": avg_messages_per_day,
            "images_sent": conversation_image_stats['sent'],
            "images_received": conversation_image_stats['received'],
            "total_image_size": conversation_image_stats['total_size'],
            "image_data": contact_info['image_data']
        })
    
    return sorted(all_conversations, key=lambda x: x['sent_count'] + x['received_count'], reverse=True)
    
def main():
    bundle_dir = get_bundle_dir()
    output_dir = get_output_dir()
    backup_folder = [d for d in os.listdir(output_dir) if d.startswith('00')][0]
    backup_root = os.path.join(output_dir, backup_folder)
    manifest_path = os.path.join(backup_root, 'Manifest.db')

    print("Step 1: Extracting relevant file paths...")
    relevant_files = get_file_paths(manifest_path)

    print("Step 2: Copying relevant files...")
    copy_relevant_files(backup_root, output_dir, relevant_files)

    sms_db_path = os.path.join(output_dir, 'sms.db')
    address_book_path = os.path.join(output_dir, 'AddressBook.sqlitedb')

    print("Step 3: Analyzing iMessage data...")
    conversations = analyze_imessage_data(sms_db_path)

    print("Step 4: Mapping contacts...")
    contacts = get_contacts(address_book_path)

    print("Step 5: Analyzing image attachments...")
    attachments = get_attachments(sms_db_path)
    image_stats = analyze_image_attachments(attachments)

    print("Step 6: Calculating all conversations...")
    all_conversations = get_all_conversations(conversations, contacts, image_stats)

    print("Step 7: Saving all conversations...")
    with open(os.path.join(output_dir, 'all_conversations.json'), 'w') as f:
        json.dump(all_conversations, f, indent=2)

    print("Analysis complete. Results saved in 'all_conversations.json'")

if __name__ == "__main__":
    main()