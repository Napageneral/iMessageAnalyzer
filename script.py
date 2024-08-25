import os
import sqlite3
import shutil
import json
from collections import defaultdict
from datetime import datetime
import re
from fuzzywuzzy import fuzz
import sys

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
    # Remove all non-digit characters
    digits = re.sub(r'\D', '', phone)
    
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

def analyze_group_chats(sms_db_path, contacts):
    conn = sqlite3.connect(sms_db_path)
    cursor = conn.cursor()
    
    query = """
    SELECT 
        c.chat_identifier,
        c.display_name,
        GROUP_CONCAT(DISTINCT h.id) AS participants,
        COUNT(DISTINCT m.ROWID) AS total_messages,
        MIN(m.date) AS first_message,
        MAX(m.date) AS last_message
    FROM 
        chat c
    JOIN 
        chat_handle_join chj ON c.ROWID = chj.chat_id
    JOIN 
        handle h ON chj.handle_id = h.ROWID
    LEFT JOIN 
        chat_message_join cmj ON c.ROWID = cmj.chat_id
    LEFT JOIN 
        message m ON cmj.message_id = m.ROWID
    WHERE 
        c.chat_identifier LIKE 'chat%'  -- This often indicates a group chat
    GROUP BY 
        c.ROWID, c.chat_identifier, c.display_name
    ORDER BY 
        total_messages DESC
    """
    
    cursor.execute(query)
    group_chats = cursor.fetchall()
    conn.close()

    formatted_group_chats = []
    for chat in group_chats:
        chat_name = chat[1] if chat[1] else f"Group Chat {chat[0]}"
        participants = chat[2].split(',') if chat[2] else []
        
        # Match participants to contact names
        matched_participants = []
        for participant in participants:
            normalized_participant = normalize_phone_number(participant)
            contact_info = contacts.get(normalized_participant, {'name': participant})
            cleaned_name = clean_contact_name(contact_info['name'])
            matched_participants.append(cleaned_name if cleaned_name else participant)
        
        formatted_group_chats.append({
            "chat_name": chat_name,
            "participants": matched_participants,
            "total_messages": chat[3] or 0,  # Use 0 if NULL
            "first_message": format_date(chat[4]),
            "last_message": format_date(chat[5])
        })

    return formatted_group_chats

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