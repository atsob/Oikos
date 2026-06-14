"""
Database Backup and Restore Module for Finance OS
Handles backing up and restoring the PostgreSQL database
"""

import os
import subprocess
import streamlit as st
import pandas as pd
from datetime import datetime
import psycopg2
from config.settings import ENV_CONFIG
from database.connection import get_connection

class DatabaseBackup:
    """Handles database backup and restore operations"""
    
    def __init__(self):
        self.db_config = {
            'dbname': ENV_CONFIG['db_name'],
            'user': ENV_CONFIG['db_user'],
            'password': ENV_CONFIG['db_password'],
            'host': ENV_CONFIG['db_host'],
            'port': ENV_CONFIG['db_port']
        }
        self.backup_dir = "database_backups"
        
        # Create backup directory if it doesn't exist
        if not os.path.exists(self.backup_dir):
            os.makedirs(self.backup_dir)
    
    def get_backup_path(self, backup_name=None):
        """Generate backup file path"""
        if backup_name:
            return os.path.join(self.backup_dir, backup_name)
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"finance_backup_{timestamp}.dump"
            return os.path.join(self.backup_dir, filename)
    
    def get_table_sizes(self):
        """Get sizes of all tables in the database"""
        conn = get_connection()
        query = """
            SELECT 
                table_name,
                pg_size_pretty(pg_total_relation_size(quote_ident(table_name))) as size,
                pg_total_relation_size(quote_ident(table_name)) as size_bytes
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_type = 'BASE TABLE'
            ORDER BY size_bytes DESC
        """
        df = pd.read_sql(query, conn)
        conn.close()
        return df
    
    def get_backup_history(self):
        """Get list of existing backups with metadata"""
        backups = []
        if os.path.exists(self.backup_dir):
            for filename in os.listdir(self.backup_dir):
                if filename.endswith('.dump'):
                    filepath = os.path.join(self.backup_dir, filename)
                    stat = os.stat(filepath)
                    backups.append({
                        'filename': filename,
                        'size': stat.st_size,
                        'size_mb': stat.st_size / (1024 * 1024),
                        'modified': datetime.fromtimestamp(stat.st_mtime),
                        'filepath': filepath
                    })
        # Sort by modified date (newest first)
        backups.sort(key=lambda x: x['modified'], reverse=True)
        return backups
    
    def create_backup(self, backup_name=None, include_blobs=True):
        """Create a database backup using pg_dump"""
        try:
            # Set password environment variable for pg_dump
            os.environ['PGPASSWORD'] = self.db_config['password']
            
            backup_path = self.get_backup_path(backup_name)
            
            # Build pg_dump command
            cmd = [
                'pg_dump',
                '-h', self.db_config['host'],
                '-p', self.db_config['port'],
                '-U', self.db_config['user'],
                '-d', self.db_config['dbname'],
                '-F', 'c',  # Custom format (compressed)
                '-f', backup_path,
                '-v'  # Verbose
            ]
            
            # Add blob inclusion/exclusion
            if not include_blobs:
                cmd.extend(['--exclude-table-data', 'historical_prices'])
                cmd.extend(['--exclude-table-data', 'historical_fx'])
            
            # Execute backup
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            # Clear password environment variable
            del os.environ['PGPASSWORD']
            
            if result.returncode == 0:
                file_size = os.path.getsize(backup_path)
                return {
                    'success': True,
                    'path': backup_path,
                    'filename': os.path.basename(backup_path),
                    'size': file_size,
                    'size_mb': file_size / (1024 * 1024),
                    'message': f"Backup created successfully: {backup_path}"
                }
            else:
                return {
                    'success': False,
                    'message': f"Backup failed: {result.stderr}"
                }
                
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'message': "Backup timed out after 5 minutes"
            }
        except Exception as e:
            return {
                'success': False,
                'message': f"Backup failed: {str(e)}"
            }
    
    def restore_backup(self, backup_file_path, drop_existing=True):
        """Restore database from a backup file using pg_restore"""
        try:
            # Set password environment variable
            os.environ['PGPASSWORD'] = self.db_config['password']
            
            # First, terminate all connections to the database
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(f"""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = '{self.db_config['dbname']}'
                AND pid <> pg_backend_pid()
            """)
            conn.commit()
            cur.close()
            conn.close()
            
            # Build pg_restore command
            cmd = [
                'pg_restore',
                '-h', self.db_config['host'],
                '-p', self.db_config['port'],
                '-U', self.db_config['user'],
                '-d', self.db_config['dbname'],
                '-c',  # Clean (drop) database objects before recreating
                '-v',  # Verbose
                backup_file_path
            ]
            
            # Execute restore
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600  # 10 minute timeout for restore
            )
            
            # Clear password environment variable
            del os.environ['PGPASSWORD']
            
            if result.returncode == 0:
                return {
                    'success': True,
                    'message': f"Database restored successfully from {os.path.basename(backup_file_path)}"
                }
            else:
                return {
                    'success': False,
                    'message': f"Restore failed: {result.stderr}"
                }
                
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'message': "Restore timed out after 10 minutes"
            }
        except Exception as e:
            return {
                'success': False,
                'message': f"Restore failed: {str(e)}"
            }
    
    def delete_backup(self, filename):
        """Delete a backup file"""
        try:
            filepath = os.path.join(self.backup_dir, filename)
            if os.path.exists(filepath):
                os.remove(filepath)
                return {
                    'success': True,
                    'message': f"Backup {filename} deleted successfully"
                }
            else:
                return {
                    'success': False,
                    'message': f"Backup file {filename} not found"
                }
        except Exception as e:
            return {
                'success': False,
                'message': f"Failed to delete backup: {str(e)}"
            }
    
    def export_to_csv(self, table_name, output_path=None):
        """Export a specific table to CSV format"""
        try:
            if not output_path:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = os.path.join(self.backup_dir, f"{table_name}_{timestamp}.csv")
            
            conn = get_connection()
            query = f"SELECT * FROM {table_name}"
            df = pd.read_sql(query, conn)
            conn.close()
            
            df.to_csv(output_path, index=False)
            
            return {
                'success': True,
                'path': output_path,
                'filename': os.path.basename(output_path),
                'rows': len(df),
                'message': f"Exported {len(df)} rows to {output_path}"
            }
        except Exception as e:
            return {
                'success': False,
                'message': f"Export failed: {str(e)}"
            }
    
    def import_from_csv(self, table_name, csv_file_path, truncate_first=True):
        """Import data from CSV file into a specific table"""
        try:
            conn = get_connection()
            df = pd.read_csv(csv_file_path)
            
            cur = conn.cursor()
            
            # Truncate table if requested
            if truncate_first:
                cur.execute(f"TRUNCATE TABLE {table_name} RESTART IDENTITY CASCADE;")
                st.info(f"Truncated table {table_name}")
            
            # Insert data
            cols = df.columns.tolist()
            placeholders = ", ".join(["%s"] * len(cols))
            insert_sql = f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES ({placeholders})"
            
            rows_imported = 0
            for _, row in df.iterrows():
                values = [None if pd.isna(v) else v for v in row.values]
                cur.execute(insert_sql, values)
                rows_imported += 1
            
            conn.commit()
            cur.close()
            conn.close()
            
            return {
                'success': True,
                'rows': rows_imported,
                'message': f"Imported {rows_imported} rows into {table_name}"
            }
        except Exception as e:
            return {
                'success': False,
                'message': f"Import failed: {str(e)}"
            }


def render_backup_restore():
    """Render the Backup and Restore UI component"""
    st.subheader("💾 Database Backup & Restore")
    
    backup_manager = DatabaseBackup()
    
    # Create tabs for different operations
    tab_backup, tab_restore, tab_export, tab_schedule = st.tabs([
        "📤 Create Backup", "📥 Restore Backup", "📊 Export/Import CSV", "⏰ Scheduled Backups"
    ])
    
    # ==================== BACKUP TAB ====================
    with tab_backup:
        st.markdown("### Create Database Backup")
        
        # Show database info
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Database Name", ENV_CONFIG['db_name'])
            st.metric("Host", ENV_CONFIG['db_host'])
        with col2:
            st.metric("User", ENV_CONFIG['db_user'])
            st.metric("Port", ENV_CONFIG['db_port'])
        
        # Table sizes
        with st.expander("📊 Current Database Size"):
            df_sizes = backup_manager.get_table_sizes()
            st.dataframe(
                df_sizes[['table_name', 'size']],
                hide_index=True,
                width='stretch'
            )
            total_size = df_sizes['size_bytes'].sum()
            st.info(f"**Total Database Size:** {total_size / (1024*1024):.2f} MB")
        
        # Backup options
        st.markdown("#### Backup Options")
        
        col1, col2 = st.columns(2)
        with col1:
            custom_name = st.text_input(
                "Custom Backup Name (optional)",
                placeholder="Leave empty for auto-generated name"
            )
        with col2:
            exclude_blobs = st.checkbox(
                "Exclude large data (prices, FX rates)",
                value=False,
                help="Creates smaller backup by excluding historical_prices and historical_fx tables"
            )
        
        # Create backup button
        if st.button("🔒 Create Backup", type="primary", width='stretch'):
            with st.spinner("Creating backup... This may take a few minutes..."):
                backup_name = custom_name if custom_name else None
                result = backup_manager.create_backup(backup_name, include_blobs=not exclude_blobs)
                
                if result['success']:
                    st.success(result['message'])
                    st.info(f"**File:** {result['filename']}")
                    st.info(f"**Size:** {result['size_mb']:.2f} MB")
                    st.info(f"**Location:** {result['path']}")
                    
                    # Provide download link
                    with open(result['path'], 'rb') as f:
                        st.download_button(
                            label="📥 Download Backup",
                            data=f,
                            file_name=result['filename'],
                            mime="application/octet-stream"
                        )
                    st.balloons()
                else:
                    st.error(result['message'])
        
        # Show existing backups
        st.markdown("### 📂 Existing Backups")
        backups = backup_manager.get_backup_history()
        
        if backups:
            # Display backups in a dataframe
            df_backups = pd.DataFrame(backups)
            df_backups['size_mb'] = df_backups['size_mb'].round(2)
            df_backups['modified'] = pd.to_datetime(df_backups['modified']).dt.strftime('%Y-%m-%d %H:%M:%S')
            
            st.dataframe(
                df_backups[['filename', 'size_mb', 'modified']],
                column_config={
                    'filename': 'Backup File',
                    'size_mb': st.column_config.NumberColumn('Size (MB)', format="%.2f"),
                    'modified': 'Date Created'
                },
                hide_index=True,
                width='stretch'
            )
            
            # Delete backup option
            col1, col2 = st.columns([3, 1])
            with col1:
                backup_to_delete = st.selectbox(
                    "Select backup to delete",
                    options=[b['filename'] for b in backups],
                    key="delete_backup_select"
                )
            with col2:
                if st.button("🗑️ Delete Selected", width='stretch'):
                    result = backup_manager.delete_backup(backup_to_delete)
                    if result['success']:
                        st.success(result['message'])
                        st.rerun()
                    else:
                        st.error(result['message'])
        else:
            st.info("No backups found. Create your first backup above.")
    
    # ==================== RESTORE TAB ====================
    with tab_restore:
        st.markdown("### Restore Database from Backup")
        st.warning("⚠️ **WARNING:** Restoring will overwrite your current database. This action cannot be undone!")
        
        # Option 1: Restore from existing backup
        st.markdown("#### Restore from Existing Backup")
        
        backups = backup_manager.get_backup_history()
        if backups:
            selected_backup = st.selectbox(
                "Select backup to restore",
                options=[b['filename'] for b in backups],
                key="restore_backup_select"
            )
            
            # Show backup details
            selected_backup_info = next(b for b in backups if b['filename'] == selected_backup)
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Backup Size", f"{selected_backup_info['size_mb']:.2f} MB")
            with col2:
                st.metric("Created", selected_backup_info['modified'].strftime('%Y-%m-%d %H:%M:%S'))
            
            # Confirmation checkbox
            confirm_restore = st.checkbox(
                "✅ I understand that this will overwrite my current database",
                key="confirm_restore"
            )
            
            if st.button("🔄 Restore Selected Backup", type="secondary", width='stretch'):
                if confirm_restore:
                    with st.spinner("Restoring database... This may take several minutes..."):
                        result = backup_manager.restore_backup(selected_backup_info['filepath'])
                        
                        if result['success']:
                            st.success(result['message'])
                            st.info("🔄 Please refresh the page to see the restored data.")
                            if st.button("Refresh Now"):
                                st.rerun()
                        else:
                            st.error(result['message'])
                else:
                    st.warning("Please confirm that you understand the consequences")
        else:
            st.info("No backups available for restore")
        
        # Option 2: Restore from uploaded file
        st.markdown("#### Restore from Uploaded Backup")
        
        uploaded_backup = st.file_uploader(
            "Upload a backup file (.dump)",
            type=['dump'],
            key="restore_upload"
        )
        
        if uploaded_backup is not None:
            # Save uploaded file
            temp_restore_path = f"/tmp/restore_{uploaded_backup.name}"
            with open(temp_restore_path, "wb") as f:
                f.write(uploaded_backup.getbuffer())
            
            st.info(f"Uploaded: {uploaded_backup.name}")
            st.info(f"Size: {uploaded_backup.size / (1024*1024):.2f} MB")
            
            confirm_upload_restore = st.checkbox(
                "✅ I understand that this will overwrite my current database",
                key="confirm_upload_restore"
            )
            
            if st.button("🔄 Restore from Uploaded File", type="secondary", width='stretch'):
                if confirm_upload_restore:
                    with st.spinner("Restoring database from uploaded file..."):
                        result = backup_manager.restore_backup(temp_restore_path)
                        
                        if result['success']:
                            st.success(result['message'])
                            st.info("🔄 Please refresh the page to see the restored data.")
                            if st.button("Refresh Now", key="refresh_after_upload"):
                                st.rerun()
                        else:
                            st.error(result['message'])
                else:
                    st.warning("Please confirm that you understand the consequences")
    
    # ==================== EXPORT/IMPORT CSV TAB ====================
    with tab_export:
        st.markdown("### Export/Import Tables as CSV")
        st.info("This is useful for selective backup/restore of individual tables")
        
        # Get list of tables
        conn = get_connection()
        query = """
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """
        tables_df = pd.read_sql(query, conn)
        conn.close()
        
        tables = tables_df['table_name'].tolist()
        
        # Export section
        st.markdown("#### 📤 Export Table to CSV")
        
        col1, col2 = st.columns([2, 1])
        with col1:
            export_table = st.selectbox("Select table to export", tables, key="export_table_select")
        with col2:
            if st.button("Export to CSV", width='stretch'):
                with st.spinner(f"Exporting {export_table}..."):
                    result = backup_manager.export_to_csv(export_table)
                    if result['success']:
                        st.success(result['message'])
                        with open(result['path'], 'rb') as f:
                            st.download_button(
                                label=f"📥 Download {result['filename']}",
                                data=f,
                                file_name=result['filename'],
                                mime="text/csv"
                            )
                    else:
                        st.error(result['message'])
        
        st.divider()
        
        # Import section
        st.markdown("#### 📥 Import Table from CSV")
        st.warning("⚠️ Importing will replace existing data in the selected table")
        
        col1, col2 = st.columns([2, 1])
        with col1:
            import_table = st.selectbox("Select target table", tables, key="import_table_select")
            truncate_first = st.checkbox("Delete existing data before import", value=True)
        with col2:
            uploaded_csv = st.file_uploader(
                "Upload CSV file",
                type=['csv'],
                key="import_csv_upload"
            )
        
        if uploaded_csv is not None:
            temp_csv_path = f"/tmp/import_{uploaded_csv.name}"
            with open(temp_csv_path, "wb") as f:
                f.write(uploaded_csv.getbuffer())
            
            st.info(f"File ready: {uploaded_csv.name}")
            
            # Preview CSV
            with st.expander("Preview CSV data"):
                df_preview = pd.read_csv(temp_csv_path)
                st.dataframe(df_preview.head(), width='stretch')
                st.caption(f"Total rows: {len(df_preview)}")
            
            if st.button("Import CSV", type="secondary", width='stretch'):
                with st.spinner(f"Importing into {import_table}..."):
                    result = backup_manager.import_from_csv(import_table, temp_csv_path, truncate_first)
                    if result['success']:
                        st.success(result['message'])
                        st.balloons()
                    else:
                        st.error(result['message'])
    
    # ==================== SCHEDULED BACKUPS TAB ====================
    with tab_schedule:
        st.markdown("### Scheduled Backups")
        st.info("Configure automatic backups using cron jobs or task scheduler")
        
        st.markdown("""
        #### Setting up Scheduled Backups
        
        You can set up automatic backups using your operating system's task scheduler.
        
        **Linux / macOS (cron):**
        
        Add this line to your crontab (`crontab -e`):
        ```bash
        # Daily backup at 2 AM
        0 2 * * * cd /path/to/your/app && python -c "from database.backup import DatabaseBackup; db = DatabaseBackup(); db.create_backup()""")

        backups = backup_manager.get_backup_history()
        if backups:
            st.markdown("#### Backup Statistics")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Backups", len(backups))
            with col2:
                total_size = sum(b['size'] for b in backups) / (1024 * 1024 * 1024)
                st.metric("Total Storage Used", f"{total_size:.2f} GB")
            with col3:
                oldest = backups[-1]['modified'] if backups else None
                if oldest:
                    days_old = (datetime.now() - oldest).days
                    st.metric("Oldest Backup", f"{days_old} days ago")        