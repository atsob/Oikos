from database.backup import DatabaseBackup

# Create backup
db = DatabaseBackup()
result = db.create_backup()
print(result['message'])