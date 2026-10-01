NAME = "Norm Backup"
VERSION = "1.1.0"
ENTRYPOINT = "backup.py:create_backup"
CAPABILITIES = ["backup", "portable source backup", "full disaster recovery", "installer package", "postgres backup"]
DESCRIPTION = "Create installer-compatible source backups or sensitive full-state backups without archiving the reproducible .venv."
