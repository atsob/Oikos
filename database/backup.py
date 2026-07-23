"""
Database Backup and Restore Module for Personal Finance
Handles backing up and restoring PostgreSQL database in Docker
"""

import logging
import os
import subprocess
import pandas as pd
from datetime import datetime
import psycopg2
from config.settings import ENV_CONFIG
from database.connection import get_connection

log = logging.getLogger(__name__)

class DatabaseBackup:
    """Handles database backup and restore operations for Docker environment"""
    
    def __init__(self):
        self.db_config = {
            'dbname': ENV_CONFIG['db_name'],
            'user': ENV_CONFIG['db_user'],
            'password': ENV_CONFIG['db_password'],
            'host': ENV_CONFIG['db_host'],
            'port': ENV_CONFIG['db_port']
        }
        self.backup_dir = "/app/database_backups"  # Inside Docker container
        self.local_backup_dir = "database_backups"  # For local downloads
        
        # Create backup directories
        for directory in [self.backup_dir, self.local_backup_dir]:
            if not os.path.exists(directory):
                os.makedirs(directory)
        
        # Detect if running in Docker
        self.in_docker = self._is_running_in_docker()
        
        # Find PostgreSQL binary (in container or local)
        self.pg_dump_path = self._find_postgresql_binary('pg_dump')
        self.pg_restore_path = self._find_postgresql_binary('pg_restore')
    
    def _is_running_in_docker(self):
        """Check if the app is running inside a Docker container"""
        try:
            with open('/proc/1/cgroup', 'rt') as f:
                return 'docker' in f.read()
        except:
            return False
    
    def _find_postgresql_binary(self, binary_name):
        """Find PostgreSQL binary path (inside container or locally)"""
        import shutil
        
        # If in Docker, use the binary directly (should be in PATH)
        if self.in_docker:
            binary_path = shutil.which(binary_name)
            if binary_path:
                return binary_path
            # In Alpine-based images, might be in /usr/local/bin
            if os.path.exists(f"/usr/local/bin/{binary_name}"):
                return f"/usr/local/bin/{binary_name}"
            return binary_name
        
        # Local Windows/Mac - check common paths
        binary_path = shutil.which(binary_name)
        if binary_path:
            return binary_path
        
        # Common PostgreSQL installation paths
        common_paths = [
            os.path.join(os.environ.get('LOCALAPPDATA', ''), r'Programs\pgAdmin 4\runtime'),
            os.path.join(os.environ.get('LOCALAPPDATA', ''), r'Programs\pgAdmin 4\bin'),
            r"C:\Program Files\PostgreSQL\16\bin",
            r"C:\Program Files\PostgreSQL\15\bin",
            r"C:\Program Files\PostgreSQL\14\bin",
            r"/usr/local/bin",
            r"/usr/bin",
            r"/opt/homebrew/bin",  # macOS Homebrew
        ]
        
        for path in common_paths:
            full_path = os.path.join(path, f"{binary_name}.exe" if os.name == 'nt' else binary_name)
            if os.path.exists(full_path):
                return full_path
        
        return binary_name
    
    def get_backup_path(self, backup_name=None):
        """Generate backup file path inside Docker container"""
        if backup_name:
            if not backup_name.endswith('.dump'):
                backup_name = f"{backup_name}.dump"
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
    #    print(f"Looking for backups in: {self.backup_dir}")     ## Debugging line to check backup directory
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
    
    def create_backup(self, backup_name=None, include_blobs=True, run_in_docker=True):
        """Create a database backup"""
        try:
            # Set password environment variable
            os.environ['PGPASSWORD'] = self.db_config['password']
            
            backup_path = self.get_backup_path(backup_name)
            
            # Method 1: Run pg_dump inside PostgreSQL container (recommended)
            if run_in_docker and self._is_postgres_in_docker():
                return self._create_backup_in_docker(backup_path, include_blobs)
            
            # Method 2: Run pg_dump from app container to PostgreSQL container
            cmd = [
                self.pg_dump_path,
                '-h', self.db_config['host'],
                '-p', self.db_config['port'],
                '-U', self.db_config['user'],
                '-d', self.db_config['dbname'],
                '-F', 'c',
                '-f', backup_path,
                '-v'
            ]
            
            if not include_blobs:
                cmd.extend(['--exclude-table-data', 'historical_prices'])
                cmd.extend(['--exclude-table-data', 'historical_fx'])
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                shell=False
            )
            
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
                    'message': f"Backup failed: {result.stderr}\n\nCommand: {' '.join(cmd)}"
                }
                
        except FileNotFoundError as e:
            return {
                'success': False,
                'message': f"PostgreSQL client not found: {str(e)}\n\nPlease ensure pg_dump is installed."
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
    
    def _is_postgres_in_docker(self):
        """Check if PostgreSQL is running in Docker"""
        try:
            # Try to find postgres container
            result = subprocess.run(
                ['docker', 'ps', '--format', '{{.Names}}', '--filter', 'status=running'],
                capture_output=True,
                text=True,
                timeout=10
            )
            containers = result.stdout.strip().split('\n')
            
            # Look for common postgres container names
            postgres_containers = [c for c in containers if 'postgres' in c.lower() or 'postgre' in c.lower()]
            
            # Also check for container with postgres image
            result = subprocess.run(
                ['docker', 'ps', '--format', '{{.Image}}', '--filter', 'status=running'],
                capture_output=True,
                text=True,
                timeout=10
            )
            images = result.stdout.strip().split('\n')
            postgres_images = [i for i in images if 'postgres' in i.lower()]
            
            return len(postgres_containers) > 0 or len(postgres_images) > 0
            
        except Exception as e:
            return False
    
    def _get_postgres_container_name(self):
        """Get the name of the running PostgreSQL container"""
        try:
            # Try to find container with postgres in name
            result = subprocess.run(
                ['docker', 'ps', '--format', '{{.Names}}', '--filter', 'status=running'],
                capture_output=True,
                text=True,
                timeout=10
            )
            containers = result.stdout.strip().split('\n')
            
            for container in containers:
                if 'postgres' in container.lower() or 'postgre' in container.lower():
                    return container
            
            # Try to find by image
            result = subprocess.run(
                ['docker', 'ps', '--format', '{{.Names}}|{{.Image}}', '--filter', 'status=running'],
                capture_output=True,
                text=True,
                timeout=10
            )
            for line in result.stdout.strip().split('\n'):
                if '|' in line:
                    name, image = line.split('|')
                    if 'postgres' in image.lower():
                        return name
            
            return None
            
        except Exception as e:
            return None
    
    def _create_backup_in_docker(self, backup_path, include_blobs):
        """Create backup by running pg_dump inside PostgreSQL container"""
        try:
            container_name = self._get_postgres_container_name()
            
            if not container_name:
                return {
                    'success': False,
                    'message': "Could not find PostgreSQL container. Please ensure it's running and has 'postgres' in its name."
                }
            
            log.info("Using PostgreSQL container: %s", container_name)

            # Build pg_dump command inside container
            dump_cmd = [
                'docker', 'exec', container_name,
                'pg_dump',
                '-U', self.db_config['user'],
                '-d', self.db_config['dbname'],
                '-F', 'c',
                '-f', '/tmp/backup.dump'
            ]
            
            if not include_blobs:
                dump_cmd.extend(['--exclude-table-data', 'historical_prices'])
                dump_cmd.extend(['--exclude-table-data', 'historical_fx'])
            
            # Run pg_dump inside container
            result = subprocess.run(
                dump_cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                return {
                    'success': False,
                    'message': f"pg_dump in container failed: {result.stderr}"
                }
            
            # Copy backup from container to host (inside app container)
            copy_cmd = [
                'docker', 'cp',
                f"{container_name}:/tmp/backup.dump",
                backup_path
            ]
            
            result = subprocess.run(
                copy_cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode != 0:
                return {
                    'success': False,
                    'message': f"Failed to copy backup from container: {result.stderr}"
                }
            
            # Clean up temp file in container
            cleanup_cmd = ['docker', 'exec', container_name, 'rm', '-f', '/tmp/backup.dump']
            subprocess.run(cleanup_cmd, capture_output=True, timeout=10)
            
            file_size = os.path.getsize(backup_path)
            return {
                'success': True,
                'path': backup_path,
                'filename': os.path.basename(backup_path),
                'size': file_size,
                'size_mb': file_size / (1024 * 1024),
                'message': f"Backup created successfully in container {container_name}"
            }
            
        except FileNotFoundError as e:
            return {
                'success': False,
                'message': f"Docker not found: {str(e)}\n\nPlease ensure Docker is installed and running."
            }
        except Exception as e:
            return {
                'success': False,
                'message': f"Backup failed: {str(e)}"
            }
        
    def restore_backup(self, backup_file_path, drop_existing=True):
        """Restore database from a backup file"""
        try:
            os.environ['PGPASSWORD'] = self.db_config['password']
            
            # Check if we should restore via Docker
            if self._is_postgres_in_docker():
                return self._restore_backup_in_docker(backup_file_path)
            
            # Terminate connections
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
                self.pg_restore_path,
                '-h', self.db_config['host'],
                '-p', self.db_config['port'],
                '-U', self.db_config['user'],
                '-d', self.db_config['dbname'],
                '-c',
                '-v',
                backup_file_path
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                shell=False
            )
            
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
                
        except Exception as e:
            return {
                'success': False,
                'message': f"Restore failed: {str(e)}"
            }
    
    def _restore_backup_in_docker(self, backup_file_path):
        """Restore database by running pg_restore inside PostgreSQL container"""
        container_name = self._get_postgres_container_name()
        
        if not container_name:
            return {
                'success': False,
                'message': "Could not find PostgreSQL container"
            }
        
        log.info("Using PostgreSQL container: %s", container_name)

        try:
            # Copy backup file to container
            copy_cmd = [
                'docker', 'cp',
                backup_file_path,
                f"{container_name}:/tmp/restore.dump"
            ]
            
            result = subprocess.run(copy_cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                return {
                    'success': False,
                    'message': f"Failed to copy backup to container: {result.stderr}"
                }
            
            # Terminate connections inside container
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
            
            # Run pg_restore inside container
            restore_cmd = [
                'docker', 'exec', container_name,
                'pg_restore',
                '-U', self.db_config['user'],
                '-d', self.db_config['dbname'],
                '-c',
                '-v',
                '/tmp/restore.dump'
            ]
            
            result = subprocess.run(
                restore_cmd,
                capture_output=True,
                text=True,
                timeout=600
            )
            
            # Clean up
            cleanup_cmd = ['docker', 'exec', container_name, 'rm', '-f', '/tmp/restore.dump']
            subprocess.run(cleanup_cmd, capture_output=True, timeout=10)
            
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

