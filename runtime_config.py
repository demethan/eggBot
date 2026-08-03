"""Database-backed runtime configuration facade."""

from eggbot_db.repositories import RebootScheduleRepository, ServerRepository, SettingsRepository


class RuntimeConfig:
    def __init__(self, connection, servers: ServerRepository):
        self.connection = connection
        self.servers = servers
        self.settings = SettingsRepository(connection)
        self.schedules = RebootScheduleRepository(connection)

    def get(self, key, default=None):
        return self.settings.get(key, default)

    def set(self, key, value):
        self.settings.set(key, value)
        self.connection.commit()

    def require_int(self, key):
        value = self.get(key)
        if value is None:
            raise RuntimeError(f"Required setting {key} is not configured")
        return int(value)

    def notification_source(self, discord_id):
        sources = self.get("serverNotificationSources", {})
        return sources.get(str(discord_id))
