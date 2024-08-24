import sys
import os
import sqlite3
from PyQt5.QtWidgets import (QApplication, QWidget, QPushButton, QVBoxLayout, QTextEdit, QFileDialog, QMessageBox, 
                             QCheckBox, QTabWidget, QTableWidget, QTableWidgetItem, QHBoxLayout, QLineEdit, QLabel,
                             QDesktopWidget, QHeaderView)
from PyQt5.QtCore import QTimer, Qt, QSize
from PyQt5.QtGui import QIcon, QScreen
import logging
import traceback

from script import analyze_manifest_db, get_file_paths, copy_relevant_files, analyze_imessage_data, get_contacts, get_all_conversations

class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setup_logging()
        self.debug_mode = False
        self.backup_folder = None
        self.local_db_path = self.get_local_db_path()
        self.init_ui()
        self.set_app_icon()
        QTimer.singleShot(0, self.delayed_init)

    def setup_logging(self):
        log_file = self.get_log_file_path()
        logging.basicConfig(filename=log_file, level=logging.DEBUG,
                            format='%(asctime)s - %(levelname)s - %(message)s')
        logging.info("Application started")

    def get_log_file_path(self):
        return os.path.join(self.get_app_data_dir(), 'app.log')

    def get_local_db_path(self):
        return os.path.join(self.get_app_data_dir(), 'local_results.db')

    def get_app_data_dir(self):
        app_data_dir = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'iMessageAnalyzer')
        os.makedirs(app_data_dir, exist_ok=True)
        return app_data_dir

    def get_imessage_export_dir(self):
        export_dir = os.path.join(self.get_app_data_dir(), 'imessage_export')
        os.makedirs(export_dir, exist_ok=True)
        return export_dir

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
        self.analyze_button.clicked.connect(self.safe_on_analyze_click)
        analysis_layout.addWidget(self.analyze_button)

        self.refresh_button = QPushButton('Refresh Databases', self)
        self.refresh_button.clicked.connect(self.safe_on_refresh_click)
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

    def on_refresh_click(self):
        logging.info("Refresh button clicked")
        self.backup_folder = self.get_default_backup_path()
        if not self.backup_folder:
            self.backup_folder = QFileDialog.getExistingDirectory(self, "Select iPhone Backup Folder")
        if not self.backup_folder:
            logging.warning("No backup folder selected. Refresh aborted.")
            self.result_text.append("No backup folder selected. Refresh aborted.")
            return

        try:
            self.log_debug(f"Refreshing databases from backup folder: {self.backup_folder}")

            output_dir = self.get_imessage_export_dir()
            os.makedirs(output_dir, exist_ok=True)

            manifest_path = os.path.join(self.backup_folder, 'Manifest.db')
            if not os.path.exists(manifest_path):
                raise FileNotFoundError(f"Manifest.db not found in {self.backup_folder}")

            relevant_files = get_file_paths(manifest_path)
            copied_files = copy_relevant_files(self.backup_folder, output_dir, relevant_files)

            self.log_debug(f"Refreshed files: {copied_files}")
            self.result_text.append("Databases refreshed successfully.")

        except Exception as e:
            raise Exception(f"An error occurred during refresh: {str(e)}")
        
        self.load_conversations()

    def on_analyze_click(self):
        logging.info("Analyze button clicked")
        if not self.backup_folder:
            self.backup_folder = self.get_default_backup_path()
        if not self.backup_folder:
            self.backup_folder = QFileDialog.getExistingDirectory(self, "Select iPhone Backup Folder")
        if not self.backup_folder:
            logging.warning("No backup folder selected. Analysis aborted.")
            self.result_text.append("No backup folder selected. Analysis aborted.")
            return

        try:
            logging.info(f"Starting analysis using backup folder: {self.backup_folder}")

            output_dir = self.get_imessage_export_dir()
            sms_db_path = os.path.join(output_dir, 'sms.db')
            address_book_path = os.path.join(output_dir, 'AddressBook.sqlitedb')

            if not os.path.exists(sms_db_path):
                raise FileNotFoundError(f"sms.db not found in {output_dir}. Please refresh the database first.")

            if not os.path.exists(address_book_path):
                raise FileNotFoundError(f"AddressBook.sqlitedb not found in {output_dir}. Please refresh the database first.")

            logging.debug("Analyzing iMessage data...")
            imessage_data = analyze_imessage_data(sms_db_path)
            
            logging.debug("Getting contacts...")
            contacts = get_contacts(address_book_path)
            
            logging.debug("Analyzing all conversations...")
            all_conversations = get_all_conversations(imessage_data, contacts)

            logging.debug(f"Total conversations analyzed: {len(all_conversations)}")
            self.save_results_to_local_db(all_conversations)

            self.result_text.append(f"Analysis completed. {len(all_conversations)} conversations analyzed and stored.")
            self.display_top_conversations(all_conversations[:10])

            logging.info("Analysis completed successfully.")
            self.load_conversations()

        except Exception as e:
            raise Exception(f"An error occurred during analysis: {str(e)}")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = App()
    ex.show()
    sys.exit(app.exec_())
