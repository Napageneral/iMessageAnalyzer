import sys
import os
import sqlite3
from PyQt5.QtWidgets import (QApplication, QWidget, QPushButton, QVBoxLayout, QTextEdit, QFileDialog, QMessageBox, 
                             QCheckBox, QTabWidget, QTableWidget, QTableWidgetItem, QHBoxLayout, QLineEdit, QLabel,
                             QDesktopWidget, QHeaderView, QDialog, QProgressBar)
from PyQt5.QtCore import QTimer, Qt, QSize, QUrl, QSettings
from PyQt5.QtGui import QIcon, QDesktopServices
import logging
import traceback

from script import get_attachments, get_file_paths, copy_relevant_files, analyze_imessage_data, get_contacts, get_all_conversations, analyze_image_attachments

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

        self.setLayout(main_layout)

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
            SELECT DISTINCT contact_name, sent_count, received_count, first_message_date, last_message_date
            FROM conversations
            ORDER BY sent_count + received_count DESC
            ''')
            conversations = cursor.fetchall()
            conn.close()

            self.conversations_table.setSortingEnabled(False)  # Disable sorting while updating
            self.conversations_table.setRowCount(len(conversations))
            for row, conv in enumerate(conversations):
                # Handle 'None' in contact name (first name, last name, or both)
                contact_name = conv[0] if conv[0] else ''
                name_parts = contact_name.split()
                cleaned_name_parts = [part for part in name_parts if part.lower() != 'none']
                contact_name = ' '.join(cleaned_name_parts) if cleaned_name_parts else 'Unknown'
                
                self.conversations_table.setItem(row, 0, QTableWidgetItem(contact_name))
                
                self.conversations_table.setItem(row, 1, NumericTableWidgetItem(conv[1]))
                self.conversations_table.setItem(row, 2, NumericTableWidgetItem(conv[2]))
                self.conversations_table.setItem(row, 3, NumericTableWidgetItem(conv[1] + conv[2]))
                
                # Handle fake date for first message
                first_message = conv[3] if conv[3] and conv[3] != 'December 31, 2000' else 'Unknown'
                self.conversations_table.setItem(row, 4, QTableWidgetItem(first_message))
                
                # Handle fake date for last message (just in case)
                last_message = conv[4] if conv[4] and conv[4] != 'December 31, 2000' else 'Unknown'
                self.conversations_table.setItem(row, 5, QTableWidgetItem(last_message))

                # Set left alignment for all columns
                for col in range(6):
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
                contact_name TEXT,
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
                    contact_name TEXT,
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
                    INSERT INTO conversations ({old_columns}, sent_count, received_count, images_sent, images_received, total_image_size)
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
        for conv in conversations:
            cursor.execute('''
            INSERT INTO conversations 
            (contact_name, sent_count, received_count, first_message_date, last_message_date, 
            avg_messages_per_day, images_sent, images_received, total_image_size)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (conv['contact_name'], conv['sent_count'], conv['received_count'],
                conv['first_message_date'], conv['last_message_date'], conv['avg_messages_per_day'],
                conv['images_sent'], conv['images_received'], conv['total_image_size']))
        conn.commit()
        conn.close()

    def display_top_conversations(self, top_conversations):
        self.result_text.append("Top 10 Conversations:")
        for i, conv in enumerate(top_conversations, 1):
            self.result_text.append(f"{i}. Contact: {conv['contact_name']}")
            self.result_text.append(f"   Messages Sent: {conv['sent_count']}")
            self.result_text.append(f"   Messages Received: {conv['received_count']}")
            self.result_text.append(f"   Images Sent: {conv['images_sent']}")
            self.result_text.append(f"   Images Received: {conv['images_received']}")
            self.result_text.append(f"   Total Image Size: {conv['total_image_size'] / (1024*1024):.2f} MB")
            self.result_text.append(f"   First Message: {conv['first_message_date']}")
            self.result_text.append(f"   Last Message: {conv['last_message_date']}")
            self.result_text.append(f"   Avg Messages/Day: {conv['avg_messages_per_day']:.2f}")
            self.result_text.append("")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = App()
    ex.show()
    sys.exit(app.exec_())