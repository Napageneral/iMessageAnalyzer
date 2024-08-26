import sys
import os
import sqlite3
from PyQt5.QtWidgets import (QApplication, QWidget, QPushButton, QVBoxLayout, QTextEdit, QMessageBox, 
                             QCheckBox, QTabWidget, QTableWidget, QTableWidgetItem, QHBoxLayout, QLineEdit, QLabel,
                             QDesktopWidget, QHeaderView, QDialog, QDialogButtonBox)
from PyQt5.QtCore import QTimer, Qt, QSize, QUrl, QSettings
from PyQt5.QtGui import QIcon, QTextFrameFormat, QPixmap, QImage, QTextImageFormat, QTextDocument, QTextCharFormat, QFont, QTextCharFormat, QFont, QTextTableFormat, QTextLength
import logging
import traceback
import pyperclip
from PIL import Image, ImageDraw, ImageFont
import io
from gc_image import GroupChatImageGenerator

from script import get_attachments, get_file_paths, copy_relevant_files, analyze_imessage_data, get_contacts, get_all_conversations, analyze_image_attachments, analyze_group_chats_basic, analyze_single_group_chat, clean_contact_name

class NumericTableWidgetItem(QTableWidgetItem):
    def __init__(self, value):
        super().__init__(str(value))
        self.value = value

    def __lt__(self, other):
        if isinstance(other, NumericTableWidgetItem):
            return self.value < other.value
        return super().__lt__(other)

class PermissionDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Full Disk Access Required")
        self.setMinimumSize(400, 300)
        
        layout = QVBoxLayout()
        
        label = QLabel("This application requires Full Disk Access to function properly. Please follow these steps:")
        label.setWordWrap(True)
        layout.addWidget(label)
        
        instructions = QTextEdit()
        instructions.setReadOnly(True)
        instructions.setHtml("""
        <ol>
            <li>Open System Preferences (System Settings on macOS Ventura or later)</li>
            <li>Go to Security & Privacy (Privacy & Security on macOS Ventura or later)</li>
            <li>Click on the Privacy tab</li>
            <li>Select Full Disk Access from the left sidebar</li>
            <li>Click the lock icon to make changes (you may need to enter your password)</li>
            <li>Click the + button and add this application</li>
            <li>Ensure the checkbox next to the application is checked</li>
            <li>Restart this application</li>
        </ol>
        """)
        layout.addWidget(instructions)
        
        self.check_button = QPushButton("Check Permissions")
        self.check_button.clicked.connect(self.check_permissions)
        layout.addWidget(self.check_button)

        self.bypass_button = QPushButton("I've granted access, continue anyway")
        self.bypass_button.clicked.connect(self.bypass_check)
        layout.addWidget(self.bypass_button)
        
        self.setLayout(layout)

    def check_permissions(self):
        if has_full_disk_access():
            QMessageBox.information(self, "Permission Granted", "Full Disk Access has been successfully granted.")
            self.accept()
        else:
            QMessageBox.warning(self, "Permission Not Granted", "Full Disk Access has not been granted. Please follow the instructions and try again.")

    def bypass_check(self):
        reply = QMessageBox.question(self, 'Bypass Permission Check', 
                                     "Are you sure you want to continue without verifying Full Disk Access? The application may not function correctly.",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.accept()

def has_full_disk_access():
    test_paths = [
        '/Library/Application Support/com.apple.TCC/TCC.db',
        os.path.expanduser('~/Library/Application Support/MobileSync/Backup/')
    ]
    results = []
    for path in test_paths:
        exists = os.path.exists(path)
        readable = os.access(path, os.R_OK)
        results.append(f"{path}: exists={exists}, readable={readable}")
    return "\n".join(results)

class GroupChatDetailsDialog(QDialog):
    def __init__(self, chat_name, participant_details, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Details for {chat_name}")
        self.setMinimumSize(600, 500)

        layout = QVBoxLayout()

        details_text = QTextEdit()
        details_text.setReadOnly(True)
        
        details = f"Group Chat: {chat_name}\n\nParticipant Details:\n"
        
        participant_details.sort(key=lambda x: (
            x['name'] != "You",
            x['name'] == "Unknown Participant",
            x['name']
        ))
        
        for p in participant_details:
            details += f"\n{p['name']}:\n"
            details += f"  Messages sent: {p['message_count']}\n"
            
            details += "  Tapbacks sent:\n"
            total_sent = sum(p['tapbacks_sent'].values())
            for tapback, count in p['tapbacks_sent'].items():
                rate = (count / p['message_count']) * 100 if p['message_count'] > 0 else 0
                details += f"    {tapback}: {count} ({rate:.2f}% of messages)\n"
            
            details += "  Tapbacks received:\n"
            total_received = sum(p['tapbacks_received'].values())
            for tapback, count in p['tapbacks_received'].items():
                rate = (count / p['message_count']) * 100 if p['message_count'] > 0 else 0
                details += f"    {tapback}: {count} ({rate:.2f}% of messages)\n"
            
            sent_rate = (total_sent / p['message_count']) * 100 if p['message_count'] > 0 else 0
            received_rate = (total_received / p['message_count']) * 100 if p['message_count'] > 0 else 0
            details += f"  Overall tapback sent rate: {sent_rate:.2f}%\n"
            details += f"  Overall tapback received rate: {received_rate:.2f}%\n"

        details_text.setPlainText(details)
        layout.addWidget(details_text)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok)
        button_box.accepted.connect(self.accept)
        layout.addWidget(button_box)

        self.setLayout(layout)

class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setup_logging()
        self.debug_mode = False

        # Change the current working directory to the user's home directory
        os.chdir(os.path.expanduser("~"))
        self.log_debug(f"Changed working directory to: {os.getcwd()}")
        

        self.backup_folder = self.get_default_backup_path()
        self.local_db_path = self.get_local_db_path()
        self.settings = QSettings("YourCompany", "iMessageAnalyzer")
        self.init_ui()
        self.set_app_icon()
        QTimer.singleShot(0, self.delayed_init)

    def setup_logging(self):
        log_file = self.get_log_file_path()
        logging.basicConfig(filename=log_file, level=logging.DEBUG,
                            format='%(asctime)s - %(levelname)s - %(message)s')
        logging.info("Application started")

    def get_log_file_path(self):
        if getattr(sys, 'frozen', False):
            # Running in a bundle
            bundle_dir = sys._MEIPASS
        else:
            # Running in normal Python environment
            bundle_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(bundle_dir, 'app.log')

    def get_local_db_path(self):
        if getattr(sys, 'frozen', False):
            # Running in a bundle
            bundle_dir = sys._MEIPASS
        else:
            # Running in normal Python environment
            bundle_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(bundle_dir, 'local_results.db')

    def init_ui(self):
        self.setWindowTitle('iMessage Analyzer')
        self.setMinimumSize(800, 600)  # Set minimum size
        
        main_layout = QVBoxLayout()

        # Create tab widget
        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget)

        # Analysis tab
        analysis_tab = QWidget()
        analysis_layout = QVBoxLayout()

        self.analyze_button = QPushButton('Analyze Conversations', self)
        self.analyze_button.clicked.connect(self.on_analyze_click)
        analysis_layout.addWidget(self.analyze_button)

        self.refresh_button = QPushButton('Refresh Databases', self)
        self.refresh_button.clicked.connect(self.on_refresh_click)
        analysis_layout.addWidget(self.refresh_button)

        self.debug_checkbox = QCheckBox('Debug Mode', self)
        self.debug_checkbox.stateChanged.connect(self.toggle_debug_mode)
        analysis_layout.addWidget(self.debug_checkbox)

        self.result_text = QTextEdit(self)
        self.result_text.setReadOnly(True)
        analysis_layout.addWidget(self.result_text)

        analysis_tab.setLayout(analysis_layout)
        self.tab_widget.addTab(analysis_tab, "Analysis")

        # All Conversations tab
        conversations_tab = QWidget()
        conversations_layout = QVBoxLayout()

        search_layout = QHBoxLayout()
        search_label = QLabel("Search:")
        self.search_input = QLineEdit()
        self.search_input.textChanged.connect(self.filter_conversations)
        search_layout.addWidget(search_label)
        search_layout.addWidget(self.search_input)
        conversations_layout.addLayout(search_layout)

        self.conversations_table = QTableWidget()
        self.conversations_table.setColumnCount(6)
        self.conversations_table.setHorizontalHeaderLabels(["Contact Name", "Messages Sent", "Messages Received", "Total Messages", "First Message", "Last Message"])
        self.conversations_table.setSortingEnabled(True)
        self.conversations_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        conversations_layout.addWidget(self.conversations_table)

        conversations_tab.setLayout(conversations_layout)
        self.tab_widget.addTab(conversations_tab, "All Conversations")

        # Update Group Chats tab
        group_chat_tab = QWidget()
        group_chat_layout = QVBoxLayout()

        self.analyze_group_chats_button = QPushButton('Analyze Group Chats', self)
        self.analyze_group_chats_button.clicked.connect(self.on_analyze_group_chats_click)
        group_chat_layout.addWidget(self.analyze_group_chats_button)

        self.group_chat_table = QTableWidget()
        self.group_chat_table.setColumnCount(5)
        self.group_chat_table.setHorizontalHeaderLabels(["Chat Name", "Participants", "Total Messages", "First Message", "Last Message"])
        self.group_chat_table.setSortingEnabled(True)
        self.group_chat_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.group_chat_table.itemDoubleClicked.connect(self.on_group_chat_double_click)
        group_chat_layout.addWidget(self.group_chat_table)

        group_chat_tab.setLayout(group_chat_layout)
        self.tab_widget.addTab(group_chat_tab, "Group Chats")

        self.setLayout(main_layout)
    
    def on_analyze_group_chats_click(self):
        try:
            if not self.backup_folder:
                self.result_text.append("No iPhone backup folder found. Please ensure you have a backup on this computer.")
                return

            logging.info("Starting group chat analysis...")

            output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'imessage_export')
            sms_db_path = os.path.join(output_dir, 'sms.db')
            address_book_path = os.path.join(output_dir, 'AddressBook.sqlitedb')

            if not os.path.exists(sms_db_path):
                self.result_text.append(f"Error: SMS database not found at {sms_db_path}")
                return

            if not os.path.exists(address_book_path):
                self.result_text.append(f"Error: Address Book database not found at {address_book_path}")
                return

            contacts = get_contacts(address_book_path)
            group_chats = analyze_group_chats_basic(sms_db_path, contacts)
            if not group_chats:
                self.result_text.append("No group chats found or error occurred during analysis.")
                return

            self.display_group_chats(group_chats)

            logging.info("Group chat analysis completed successfully.")
            self.result_text.append("Group chat analysis completed successfully.")

        except Exception as e:
            error_msg = f"An error occurred during group chat analysis: {str(e)}\n{traceback.format_exc()}"
            logging.error(error_msg)
            self.result_text.append(error_msg)
            QMessageBox.critical(self, "Error", error_msg)

    def display_group_chats(self, group_chats):
        self.group_chat_table.setRowCount(len(group_chats))
        for row, chat in enumerate(group_chats):
            self.group_chat_table.setItem(row, 0, QTableWidgetItem(str(chat['chat_name'])))
            self.group_chat_table.setItem(row, 1, QTableWidgetItem(', '.join(chat['participants'])))
            self.group_chat_table.setItem(row, 2, NumericTableWidgetItem(chat['total_messages']))
            self.group_chat_table.setItem(row, 3, QTableWidgetItem(str(chat['first_message'])))
            self.group_chat_table.setItem(row, 4, QTableWidgetItem(str(chat['last_message'])))
            
            # Store chat_identifier as item data for later use
            self.group_chat_table.item(row, 0).setData(Qt.UserRole, chat['chat_identifier'])

        self.group_chat_table.resizeColumnsToContents()
        self.group_chat_table.sortItems(2, Qt.DescendingOrder)


    def format_tapback_stats(self, tapbacks, total_messages):
        formatted_stats = []
        for tapback, count in tapbacks.items():
            percentage = (count / total_messages) * 100 if total_messages > 0 else 0
            formatted_stats.append(f"{tapback}: {count} ({percentage:.2f}% of messages)")
        return formatted_stats
    
    def format_group_chat_for_imessage(chat_name, participant_details):
        formatted_text = f"Group Chat: {chat_name}\n\n"

        for p in sorted(participant_details, key=lambda x: -x['message_count']):
            if p['message_count'] == 0:
                continue  # Skip participants with no messages

            formatted_text += f"{p['name']}:\n"
            formatted_text += f"Messages sent: {p['message_count']}\n\n"
            
            formatted_text += "Tapbacks:\n"
            formatted_text += f"{'Type':<14}{'Sent':>8} {'':>7} | {'Received':>8} {'':>7}\n"
            
            all_tapbacks = set(p['tapbacks_sent'].keys()) | set(p['tapbacks_received'].keys())
            for tapback in sorted(all_tapbacks):
                if tapback.startswith("Unknown"):
                    continue
                sent = p['tapbacks_sent'].get(tapback, 0)
                received = p['tapbacks_received'].get(tapback, 0)
                sent_percent = (sent / p['message_count']) * 100 if p['message_count'] > 0 else 0
                received_percent = (received / p['message_count']) * 100 if p['message_count'] > 0 else 0
                formatted_text += f"{tapback:<14}{sent:>4} ({sent_percent:>5.1f}%) | {received:>4} ({received_percent:>5.1f}%)\n"
            
            formatted_text += f"\nOverall tapback sent rate:     {p['total_tapbacks_sent'] / p['message_count'] * 100:>5.1f}%\n"
            formatted_text += f"Overall tapback received rate: {p['total_tapbacks_received'] / p['message_count'] * 100:>5.1f}%\n\n"
            formatted_text += "-" * 50 + "\n\n"

        return formatted_text


    def display_group_chat_details(self, chat_name, participant_details):
        details = QTextEdit()
        details.setReadOnly(True)
        
        cursor = details.textCursor()
        
        # Set title
        title_format = QTextCharFormat()
        title_format.setFontWeight(QFont.Bold)
        title_format.setFontPointSize(16)
        cursor.insertText(f"Group Chat: {chat_name}\n\n", title_format)
        
        # Sort participants by message count (descending), then by name
        # Move "Unknown Participant" to the end regardless of message count
        sorted_participants = sorted(
            participant_details, 
            key=lambda x: (-x['message_count'] if x['name'] != "Unknown Participant" else 0, x['name'])
        )
        
        for p in sorted_participants:
            if p['message_count'] == 0:
                continue  # Skip participants with no messages
            
            # Participant name
            name_format = QTextCharFormat()
            name_format.setFontWeight(QFont.Bold)
            name_format.setFontPointSize(14)
            cursor.insertText(f"{p['name']}:\n", name_format)
            
            # Messages sent
            cursor.insertText(f"Messages sent: {p['message_count']}\n")
            
            # Tapbacks section
            tapback_format = QTextCharFormat()
            tapback_format.setFontWeight(QFont.Bold)
            cursor.insertText("\nTapbacks:\n", tapback_format)
            
            # Create a table for tapback comparison
            table_format = QTextTableFormat()
            table_format.setCellPadding(5)
            table_format.setCellSpacing(0)
            table_format.setBorderStyle(QTextFrameFormat.BorderStyle_Solid)
            table_format.setWidth(QTextLength(QTextLength.PercentageLength, 100))
            
            all_tapbacks = set(p['tapbacks_sent'].keys()) | set(p['tapbacks_received'].keys())
            all_tapbacks = [t for t in all_tapbacks if not t.startswith("Unknown")]
            
            table = cursor.insertTable(len(all_tapbacks) + 1, 3, table_format)
            
            # Table header
            cursor.insertText("Type")
            cursor.movePosition(cursor.NextCell)
            cursor.insertText("Sent")
            cursor.movePosition(cursor.NextCell)
            cursor.insertText("Received")
            cursor.movePosition(cursor.NextCell)
            
            for tapback in sorted(all_tapbacks):
                cursor.insertText(tapback)
                cursor.movePosition(cursor.NextCell)
                
                sent = p['tapbacks_sent'].get(tapback, 0)
                sent_percent = (sent / p['message_count']) * 100 if p['message_count'] > 0 else 0
                cursor.insertText(f"{sent} ({sent_percent:.2f}%)")
                cursor.movePosition(cursor.NextCell)
                
                received = p['tapbacks_received'].get(tapback, 0)
                received_percent = (received / p['message_count']) * 100 if p['message_count'] > 0 else 0
                cursor.insertText(f"{received} ({received_percent:.2f}%)")
                cursor.movePosition(cursor.NextCell)
            
            cursor.movePosition(cursor.End)
            
            # Overall rates
            cursor.insertText("\n")
            cursor.insertText(f"Overall tapback sent rate: {p['total_tapbacks_sent'] / p['message_count'] * 100:.2f}%\n")
            cursor.insertText(f"Overall tapback received rate: {p['total_tapbacks_received'] / p['message_count'] * 100:.2f}%\n\n")
        
        return details
    
    def generate_group_chat_image(self, chat_name, participant_details):
            # Filter out unknown participants
            participant_details = [p for p in participant_details if p['name'] != "Unknown Participant"]
            
            # Set up the image
            width, height = 1000, 200 + (len(participant_details) * 400)
            image = Image.new('RGB', (width, height), color='#F3F4F6')  # Light gray background
            draw = ImageDraw.Draw(image)

            try:
                title_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 36)
                header_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 28)
                body_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 20)
            except IOError:
                title_font = ImageFont.load_default()
                header_font = ImageFont.load_default()
                body_font = ImageFont.load_default()

            # Color palette
            colors = {
                'title': '#1F2937',
                'header': '#374151',
                'body': '#4B5563',
                'accent': '#3B82F6',
                'bar_sent': '#60A5FA',
                'bar_received': '#34D399'
            }

            # Emoji to text mapping
            emoji_map = {
                '🔱': 'Trident',
                '❤️': 'Heart',
                '👍': 'Thumbs Up',
                '👎': 'Thumbs Down',
                '😂': 'Laugh',
                '!!': 'Exclamation'
            }

            # Draw title
            chat_name_text = emoji_map.get(chat_name, chat_name)
            draw.text((30, 30), f"Group Chat: {chat_name_text}", font=title_font, fill=colors['title'])

            y_offset = 100
            for p in sorted(participant_details, key=lambda x: -x['message_count']):
                if p['message_count'] == 0:
                    continue

                # Draw participant section background
                draw.rectangle([20, y_offset, width - 20, y_offset + 380], fill='white', outline=colors['accent'])

                # Draw participant name and message count
                draw.text((40, y_offset + 20), f"{p['name']}", font=header_font, fill=colors['header'])
                draw.text((40, y_offset + 60), f"Messages sent: {p['message_count']}", font=body_font, fill=colors['body'])

                # Draw tapbacks table
                draw.text((40, y_offset + 100), "Tapbacks:", font=header_font, fill=colors['header'])
                draw.text((40, y_offset + 140), f"{'Type':<14}{'Sent':>8} {'':>7} | {'Received':>8} {'':>7}", font=body_font, fill=colors['body'])

                table_y = y_offset + 180
                max_value = max(max(p['tapbacks_sent'].values(), default=0), max(p['tapbacks_received'].values(), default=0), 1)  # Ensure max_value is at least 1
                all_tapbacks = set(p['tapbacks_sent'].keys()) | set(p['tapbacks_received'].keys())
                
                for tapback in sorted(all_tapbacks):
                    if tapback.startswith("Unknown"):
                        continue
                    sent = p['tapbacks_sent'].get(tapback, 0)
                    received = p['tapbacks_received'].get(tapback, 0)
                    sent_percent = (sent / p['message_count']) * 100 if p['message_count'] > 0 else 0
                    received_percent = (received / p['message_count']) * 100 if p['message_count'] > 0 else 0

                    # Draw bars
                    sent_width = (sent / max_value) * 300
                    received_width = (received / max_value) * 300
                    draw.rectangle([300, table_y, 300 + sent_width, table_y + 20], fill=colors['bar_sent'])
                    draw.rectangle([620, table_y, 620 + received_width, table_y + 20], fill=colors['bar_received'])

                    # Draw text
                    tapback_text = emoji_map.get(tapback, tapback)
                    text = f"{tapback_text:<14}{sent:>4} ({sent_percent:>5.1f}%) | {received:>4} ({received_percent:>5.1f}%)"
                    draw.text((40, table_y), text, font=body_font, fill=colors['body'])
                    table_y += 30

                # Draw overall rates
                overall_y = table_y + 20
                sent_rate = p['total_tapbacks_sent'] / p['message_count'] * 100 if p['message_count'] > 0 else 0
                received_rate = p['total_tapbacks_received'] / p['message_count'] * 100 if p['message_count'] > 0 else 0
                draw.text((40, overall_y), f"Overall tapback sent rate: {sent_rate:.1f}%", font=body_font, fill=colors['accent'])
                draw.text((40, overall_y + 30), f"Overall tapback received rate: {received_rate:.1f}%", font=body_font, fill=colors['accent'])

                y_offset = overall_y + 80

            # Save image to bytes
            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format='PNG')
            img_byte_arr = img_byte_arr.getvalue()

            return img_byte_arr

    def copy_formatted_image(self, chat_name, participant_details):
        try:
            generator = GroupChatImageGenerator()
            img_bytes = generator.generate_group_chat_image(chat_name, participant_details)

            # Create a QImage from the bytesß
            q_image = QImage.fromData(img_bytes)
            
            # Create a QPixmap from the QImage
            pixmap = QPixmap.fromImage(q_image)
            
            # Copy the QPixmap to clipboard
            QApplication.clipboard().setPixmap(pixmap)
            
            QMessageBox.information(self, "Success", "Image copied to clipboard!")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"An error occurred while creating the image: {str(e)}")

    def on_group_chat_double_click(self, item):
        row = item.row()
        chat_name = self.group_chat_table.item(row, 0).text()
        chat_identifier = self.group_chat_table.item(row, 0).data(Qt.UserRole)

        try:
            output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'imessage_export')
            sms_db_path = os.path.join(output_dir, 'sms.db')
            address_book_path = os.path.join(output_dir, 'AddressBook.sqlitedb')

            contacts = get_contacts(address_book_path)
            participant_details = analyze_single_group_chat(sms_db_path, chat_identifier, contacts)

            logging.debug(f"Participant details for {chat_name}: {participant_details}")

            dialog = QDialog(self)
            dialog.setWindowTitle(f"Details for {chat_name}")
            dialog.setMinimumSize(800, 600)

            layout = QVBoxLayout()

            details_widget = self.display_group_chat_details(chat_name, participant_details)
            layout.addWidget(details_widget)

            copy_button = QPushButton("Copy Formatted Text to Clipboard")
            copy_button.clicked.connect(lambda: self.copy_formatted_text(chat_name, participant_details))
            layout.addWidget(copy_button)

            copy_image_button = QPushButton("Copy Statistics Image to Clipboard")
            copy_image_button.clicked.connect(lambda: self.copy_formatted_image(chat_name, participant_details))
            layout.addWidget(copy_image_button)

            button_box = QDialogButtonBox(QDialogButtonBox.Ok)
            button_box.accepted.connect(dialog.accept)
            layout.addWidget(button_box)

            dialog.setLayout(layout)
            dialog.exec_()

        except Exception as e:
            error_msg = f"An error occurred while fetching group chat details: {str(e)}\n{traceback.format_exc()}"
            logging.error(error_msg)
            QMessageBox.critical(self, "Error", error_msg)

    def copy_formatted_text(self, chat_name, participant_details):
        try:
            formatted_text = self.format_group_chat_for_imessage(chat_name, participant_details)
            pyperclip.copy(formatted_text)
            QMessageBox.information(self, "Success", "Formatted text copied to clipboard!")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"An error occurred while copying: {str(e)}")

    def format_group_chat_for_imessage(self, chat_name, participant_details):
        formatted_text = f"Group Chat: {chat_name}\n\n"

        for p in sorted(participant_details, key=lambda x: -x['message_count']):
            if p['message_count'] == 0:
                continue  # Skip participants with no messages

            formatted_text += f"{p['name']}:\n"
            formatted_text += f"Messages sent: {p['message_count']}\n\n"
            
            formatted_text += "Tapbacks:\n"
            formatted_text += f"{'Type':<14}{'Sent':>8} {'':>7} | {'Received':>8} {'':>7}\n"
            
            all_tapbacks = set(p['tapbacks_sent'].keys()) | set(p['tapbacks_received'].keys())
            for tapback in sorted(all_tapbacks):
                if tapback.startswith("Unknown"):
                    continue
                sent = p['tapbacks_sent'].get(tapback, 0)
                received = p['tapbacks_received'].get(tapback, 0)
                sent_percent = (sent / p['message_count']) * 100 if p['message_count'] > 0 else 0
                received_percent = (received / p['message_count']) * 100 if p['message_count'] > 0 else 0
                formatted_text += f"{tapback:<14}{sent:>4} ({sent_percent:>5.1f}%) | {received:>4} ({received_percent:>5.1f}%)\n"
            
            formatted_text += f"\nOverall tapback sent rate:     {p['total_tapbacks_sent'] / p['message_count'] * 100:>5.1f}%\n"
            formatted_text += f"Overall tapback received rate: {p['total_tapbacks_received'] / p['message_count'] * 100:>5.1f}%\n\n"
            formatted_text += "-" * 50 + "\n\n"

        return formatted_text

    def set_app_icon(self):
        if getattr(sys, 'frozen', False):
            # Running in a bundle
            bundle_dir = sys._MEIPASS
        else:
            # Running in normal Python environment
            bundle_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(bundle_dir, 'assets', 'icon.png')
        if os.path.exists(icon_path):
            app_icon = QIcon(icon_path)
            self.setWindowIcon(app_icon)
            # Set the app icon for the dock (macOS specific)
            if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
                QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
            QApplication.setWindowIcon(app_icon)
        else:
            print(f"Warning: Icon file not found at {icon_path}")

    def load_conversations(self):
        try:
            conn = sqlite3.connect(self.local_db_path)
            cursor = conn.cursor()
            cursor.execute('''
            SELECT contact_name, sent_count, received_count, first_message_date, last_message_date,
                avg_messages_per_day, images_sent, images_received, total_image_size
            FROM conversations
            ORDER BY sent_count + received_count DESC
            ''')
            conversations = cursor.fetchall()
            conn.close()

            self.conversations_table.setSortingEnabled(False)  # Disable sorting while updating
            self.conversations_table.setRowCount(len(conversations))
            self.conversations_table.setColumnCount(9)  # Update column count
            self.conversations_table.setHorizontalHeaderLabels([
                "Contact Name", "Messages Sent", "Messages Received", "Total Messages", 
                "Images Sent", "Images Received", "Total Image Size (MB)", "First Message", "Last Message"
            ])
            
            for row, conv in enumerate(conversations):
                # Process the contact name
                contact_name = conv[0] if conv[0] else 'Unknown'
                name_parts = contact_name.split()
                cleaned_name_parts = [part for part in name_parts if part.lower() != 'none']
                cleaned_contact_name = ' '.join(cleaned_name_parts) if cleaned_name_parts else 'Unknown'
                
                self.conversations_table.setItem(row, 0, QTableWidgetItem(cleaned_contact_name))
                self.conversations_table.setItem(row, 1, NumericTableWidgetItem(conv[1]))
                self.conversations_table.setItem(row, 2, NumericTableWidgetItem(conv[2]))
                self.conversations_table.setItem(row, 3, NumericTableWidgetItem(conv[1] + conv[2]))
                self.conversations_table.setItem(row, 4, NumericTableWidgetItem(conv[6]))  # Images sent
                self.conversations_table.setItem(row, 5, NumericTableWidgetItem(conv[7]))  # Images received
                self.conversations_table.setItem(row, 6, NumericTableWidgetItem(round(conv[8] / (1024*1024), 2)))  # Total image size in MB
                self.conversations_table.setItem(row, 7, QTableWidgetItem(conv[3] if conv[3] and conv[3] != 'December 31, 2000' else 'Unknown'))
                self.conversations_table.setItem(row, 8, QTableWidgetItem(conv[4] if conv[4] and conv[4] != 'December 31, 2000' else 'Unknown'))

                # Set left alignment for all columns
                for col in range(9):
                    item = self.conversations_table.item(row, col)
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

            self.conversations_table.setSortingEnabled(True)  # Re-enable sorting
            self.adjust_window_size()
        except Exception as e:
            self.result_text.append(f"Error loading conversations: {str(e)}")

    def adjust_window_size(self):
        # Calculate the total width needed for the table
        table_width = self.conversations_table.horizontalHeader().length() + 20  # Add some padding
        
        # Calculate the total height needed for the table
        table_height = self.conversations_table.verticalHeader().length() + 20  # Add some padding
        
        # Add extra space for window borders, search bar, etc.
        total_width = table_width + 50
        total_height = table_height + 100

        # Get the screen size
        screen = QDesktopWidget().availableGeometry()        
        # Limit the size to 80% of the screen size
        max_width = int(screen.width() * 0.8)
        max_height = int(screen.height() * 0.8)
        
        # Set the window size
        new_size = QSize(min(total_width, max_width), min(total_height, max_height))
        if new_size.width() > self.width() or new_size.height() > self.height():
            self.resize(new_size)
        
        # Center the window on the screen
        self.center_on_screen()

    def center_on_screen(self):
        screen = QDesktopWidget().screenNumber(QDesktopWidget().cursor().pos())
        center_point = QDesktopWidget().screenGeometry(screen).center()
        frame_geometry = self.frameGeometry()
        frame_geometry.moveCenter(center_point)
        self.move(frame_geometry.topLeft())

    def delayed_init(self):
        try:
            self.init_local_db()
            self.result_text.append("Application initialized successfully.")
            if self.backup_folder:
                self.result_text.append(f"Backup folder found: {self.backup_folder}")
            else:
                self.result_text.append("No backup folder found. Please ensure you have an iPhone backup on this computer.")
                self.result_text.append("Debug information:")
                self.result_text.append(f"Current working directory: {os.getcwd()}")
                self.result_text.append(f"Is frozen (running as .app): {getattr(sys, 'frozen', False)}")
                if getattr(sys, 'frozen', False):
                    self.result_text.append(f"Bundle directory: {sys._MEIPASS}")
                self.result_text.append(f"Home directory: {os.path.expanduser('~')}")
                self.result_text.append(f"Possible backup locations:")
                for location in [os.path.expanduser("~/Library/Application Support/MobileSync/Backup/"), "/Library/Application Support/MobileSync/Backup/"]:
                    self.result_text.append(f"  - {location} (exists: {os.path.exists(location)}, readable: {os.access(location, os.R_OK)})")
            
            self.result_text.append("Full Disk Access check:")
            self.result_text.append(has_full_disk_access())
            
            logging.info("Application initialized successfully")
            self.load_conversations()
        except Exception as e:
            error_msg = f"Error during initialization: {str(e)}\n{traceback.format_exc()}"
            logging.error(error_msg)
            self.result_text.append(error_msg)
            QMessageBox.critical(self, "Initialization Error", error_msg)

    def show_permission_dialog(self):
        dialog = PermissionDialog()
        result = dialog.exec_()
        if result != QDialog.Accepted:
            sys.exit()

    def init_local_db(self):
        db_path = self.get_local_db_path()
        logging.info(f"Initializing database at {db_path}")
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Check if the table already exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'")
            table_exists = cursor.fetchone()

            if not table_exists:
                cursor.execute('''
                CREATE TABLE conversations
                (id INTEGER PRIMARY KEY,
                contact_name TEXT UNIQUE,
                sent_count INTEGER,
                received_count INTEGER,
                first_message_date TEXT,
                last_message_date TEXT,
                avg_messages_per_day REAL,
                images_sent INTEGER,
                images_received INTEGER,
                total_image_size INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)
                ''')
            else:
                # If the table exists, we need to check its structure
                cursor.execute('PRAGMA table_info(conversations)')
                columns = {column[1] for column in cursor.fetchall()}

                # Check if we need to update the table
                new_columns = {'sent_count', 'received_count', 'images_sent', 'images_received', 'total_image_size'}
                if not new_columns.issubset(columns):
                    # Rename the old table
                    cursor.execute('ALTER TABLE conversations RENAME TO conversations_old')

                    # Create the new table with the updated structure
                    cursor.execute('''
                    CREATE TABLE conversations
                    (id INTEGER PRIMARY KEY,
                    contact_name TEXT UNIQUE,
                    sent_count INTEGER,
                    received_count INTEGER,
                    first_message_date TEXT,
                    last_message_date TEXT,
                    avg_messages_per_day REAL,
                    images_sent INTEGER,
                    images_received INTEGER,
                    total_image_size INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)
                    ''')

                    # Copy data from the old table to the new one, handling missing columns
                    old_columns = ', '.join(columns)
                    cursor.execute(f'''
                    INSERT OR IGNORE INTO conversations ({old_columns}, sent_count, received_count, images_sent, images_received, total_image_size)
                    SELECT {old_columns}, 
                           CASE WHEN 'message_count' IN ({old_columns}) THEN message_count ELSE 0 END,
                           0, 0, 0, 0
                    FROM conversations_old
                    ''')

                    # Drop the old table
                    cursor.execute('DROP TABLE conversations_old')

            conn.commit()
            logging.info("Database initialized successfully")
        except sqlite3.Error as e:
            logging.error(f"SQLite error: {e}", exc_info=True)
        except Exception as e:
            logging.error(f"Unexpected error: {e}", exc_info=True)
        finally:
            if conn:
                conn.close()

    def filter_conversations(self):
        search_text = self.search_input.text().lower()
        for row in range(self.conversations_table.rowCount()):
            contact_name = self.conversations_table.item(row, 0).text().lower()
            self.conversations_table.setRowHidden(row, search_text not in contact_name)

    def toggle_debug_mode(self, state):
        self.debug_mode = state == 2

    def log_debug(self, message):
        logging.debug(message)
        if self.debug_mode:
            self.result_text.append(f"DEBUG: {message}")

    def get_default_backup_path(self):
        if sys.platform == "darwin":  # macOS
            possible_backup_roots = [
                os.path.expanduser("~/Library/Application Support/MobileSync/Backup/"),
                "/Library/Application Support/MobileSync/Backup/",
            ]
            
            for backup_root in possible_backup_roots:
                self.log_debug(f"Checking backup root: {backup_root}")
                
                if not os.path.exists(backup_root):
                    self.log_debug(f"Backup root does not exist: {backup_root}")
                    continue
                
                if not os.access(backup_root, os.R_OK):
                    self.log_debug(f"No read access to backup root: {backup_root}")
                    continue
                
                try:
                    # Find the first subdirectory in the backup root
                    for item in os.listdir(backup_root):
                        item_path = os.path.join(backup_root, item)
                        if os.path.isdir(item_path):
                            self.log_debug(f"Found backup folder: {item_path}")
                            return item_path
                except PermissionError:
                    self.log_debug(f"Permission error when listing directory: {backup_root}")
                except Exception as e:
                    self.log_debug(f"Unexpected error when checking backup root: {backup_root}, Error: {str(e)}")
            
            self.log_debug("No backup folder found in any of the possible backup roots")
        else:
            self.log_debug("Unsupported platform for automatic backup detection")
        
        return None
    

    def on_refresh_click(self):
        try:
            if not self.backup_folder:
                self.result_text.append("No iPhone backup folder found. Please ensure you have a backup on this computer.")
                return

            self.log_debug(f"Refreshing databases from backup folder: {self.backup_folder}")

            output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'imessage_export')
            os.makedirs(output_dir, exist_ok=True)

            manifest_path = os.path.join(self.backup_folder, 'Manifest.db')
            relevant_files = get_file_paths(manifest_path)
            copied_files = copy_relevant_files(self.backup_folder, output_dir, relevant_files)

            self.log_debug(f"Refreshed files: {copied_files}")
            self.result_text.append("Databases refreshed successfully.")

        except Exception as e:
            error_msg = f"An error occurred during refresh: {str(e)}\n{traceback.format_exc()}"
            self.log_debug(error_msg)
            self.result_text.append(error_msg)
            QMessageBox.critical(self, "Error", error_msg)
        
        self.load_conversations()

    def on_analyze_click(self):
        try:
            if not self.backup_folder:
                self.result_text.append("No iPhone backup folder found. Please ensure you have a backup on this computer.")
                return

            logging.info(f"Starting analysis using backup folder: {self.backup_folder}")

            output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'imessage_export')
            sms_db_path = os.path.join(output_dir, 'sms.db')
            address_book_path = os.path.join(output_dir, 'AddressBook.sqlitedb')

            logging.debug("Analyzing iMessage data...")
            imessage_data = analyze_imessage_data(sms_db_path)
            logging.debug(f"Analyzed {len(imessage_data)} conversations")
            
            logging.debug("Getting contacts...")
            contacts = get_contacts(address_book_path)
            logging.debug(f"Retrieved {len(contacts)} contacts")
            
            logging.debug("Analyzing image attachments...")
            image_stats = analyze_image_attachments(sms_db_path)
            logging.debug(f"Analyzed attachments for {len(image_stats)} conversations")
            
            logging.debug("Analyzing all conversations...")
            all_conversations = get_all_conversations(imessage_data, contacts, image_stats)

            logging.debug(f"Total conversations analyzed: {len(all_conversations)}")
            self.save_results_to_local_db(all_conversations)

            self.result_text.append(f"Analysis completed. {len(all_conversations)} conversations analyzed and stored.")
            self.display_top_conversations(all_conversations[:10])  # Display top 10 for quick view

            logging.info("Analysis completed successfully.")
            self.load_conversations()

        except Exception as e:
            error_msg = f"An error occurred during analysis: {str(e)}\n{traceback.format_exc()}"
            logging.error(error_msg)
            self.result_text.append(error_msg)
            QMessageBox.critical(self, "Error", error_msg)

    def save_results_to_local_db(self, conversations):
        conn = sqlite3.connect(self.local_db_path)
        cursor = conn.cursor()
        
        # First, clear the existing data
        cursor.execute('DELETE FROM conversations')
        
        # Prepare the data, summing up values for each contact
        contact_data = {}
        for conv in conversations:
            # Clean the contact name
            name_parts = conv['contact_name'].split()
            cleaned_name_parts = [part for part in name_parts if part.lower() != 'none']
            contact_name = ' '.join(cleaned_name_parts) if cleaned_name_parts else 'Unknown'
            
            if contact_name not in contact_data:
                conv['contact_name'] = contact_name  # Use the cleaned name
                contact_data[contact_name] = conv
            else:
                # Sum up the numeric values
                for key in ['sent_count', 'received_count', 'images_sent', 'images_received', 'total_image_size']:
                    contact_data[contact_name][key] += conv[key]
                # Keep the earlier first_message_date and the later last_message_date
                contact_data[contact_name]['first_message_date'] = min(contact_data[contact_name]['first_message_date'], conv['first_message_date'])
                contact_data[contact_name]['last_message_date'] = max(contact_data[contact_name]['last_message_date'], conv['last_message_date'])
        
        # Now insert the aggregated data
        for contact_name, conv in contact_data.items():
            cursor.execute('''
            REPLACE INTO conversations 
            (contact_name, sent_count, received_count, first_message_date, last_message_date, 
            avg_messages_per_day, images_sent, images_received, total_image_size)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (contact_name, conv['sent_count'], conv['received_count'],
                conv['first_message_date'], conv['last_message_date'], conv['avg_messages_per_day'],
                conv['images_sent'], conv['images_received'], conv['total_image_size']))
        
        conn.commit()
        conn.close()

    def display_top_conversations(self, top_conversations):
        self.result_text.clear()
        cursor = self.result_text.textCursor()
        
        for i, conv in enumerate(top_conversations, 1):
            # Insert contact picture
            if conv.get('image_data'):
                image = QImage.fromData(conv['image_data'])
                image = image.scaled(50, 50, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            else:
                image = QImage(50, 50, QImage.Format_RGB32)
                image.fill(Qt.lightGray)
            
            document = self.result_text.document()
            image_format = QTextImageFormat()
            image_format.setWidth(50)
            image_format.setHeight(50)
            document.addResource(QTextDocument.ImageResource, QUrl(f"contact_image_{i}"), image)
            image_format.setName(f"contact_image_{i}")
            cursor.insertImage(image_format)
            
            # Insert conversation info
            cursor.insertText(f" {i}. Contact: {conv['contact_name']}\n")
            cursor.insertText(f"   Messages Sent: {conv['sent_count']}\n")
            cursor.insertText(f"   Messages Received: {conv['received_count']}\n")
            cursor.insertText(f"   Images Sent: {conv['images_sent']}\n")
            cursor.insertText(f"   Images Received: {conv['images_received']}\n")
            cursor.insertText(f"   Total Image Size: {conv['total_image_size'] / (1024*1024):.2f} MB\n")
            cursor.insertText(f"   First Message: {conv['first_message_date']}\n")
            cursor.insertText(f"   Last Message: {conv['last_message_date']}\n")
            cursor.insertText(f"   Avg Messages/Day: {conv['avg_messages_per_day']:.2f}\n\n")

        self.result_text.setTextCursor(cursor)
        self.result_text.ensureCursorVisible()

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    app = QApplication(sys.argv)
    ex = App()
    ex.show()
    sys.exit(app.exec_())