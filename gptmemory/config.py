import discord
from copy import deepcopy
from datetime import datetime
from typing import Any, Generic, Self, TypeVar, overload
from redbot.core import Config
from redbot.core.bot import Red
from redbot.core.config import Group


T = TypeVar("T")

class ConfigField(Generic[T]):
    """
    Represents a cached config value that may also be saved back to disk asynchronously.
    Before the parent config is initialized, the value will be the default value for this field.
    """
    def __init__(self, default: T):
        self._value = default
        self._group: Config | Group | None = None
        self._name = ""
        self._loaded = False

    @property
    def name(self) -> str:
        """The internal name of this field"""
        return self._name

    @property
    def value(self) -> T:
        """The value of this field in memory."""
        return self._value

    async def set(self, value: T) -> None:
        """Changes the value of this field in memory and saves it to disk."""
        if not self._group or not self._loaded:
            raise RuntimeError("Config has not been loaded")
        self._value = value
        await self._group.__getattr__(self._name).set(self._raw_value())

    async def save(self) -> None:
        """Saves the value of this field to disk, useful when a mutable value has been recently changed in memory."""
        if not self._group or not self._loaded:
            raise RuntimeError("Config has not been loaded")
        await self._group.__getattr__(self._name).set(self._raw_value())

    def _raw_value(self) -> Any:
        if isinstance(self.value, datetime):
            return self.value.isoformat()
        return self.value
    
    def _load_raw_value(self, value: T) -> None:
        if type(self._value) is datetime and isinstance(value, str):
            self._value = datetime.fromisoformat(value)
        else:
            self._value = value


class CogConfigBase:
    """
    Base class for a group of config fields.
    """
    def __init__(self, values: dict[str, Any], group: Config | Group | None = None):
        self._init_fields(values, group)

    def _init_fields(self, values: dict[str, Any], group: Config | Group | None = None):
        for name, field in vars(type(self)).items():
            if isinstance(field, ConfigField):
                new_field = deepcopy(field)
                object.__setattr__(self, name, new_field)
                new_field._loaded = True
                new_field._group = group
                new_field._name = name
                if name in values:
                    new_field._load_raw_value(values[name])

    @classmethod
    async def load(cls, group: Config | Group) -> Self:
        """Creates an instance of this class, loading its values from disk."""
        values = await group.all()
        return cls(values, group)

    @classmethod
    def defaults(cls) -> dict:
        """Returns a dictionary of the field names and field default values of this class."""
        return {
            name: field._raw_value()
            for name, field in vars(cls).items()
            if isinstance(field, ConfigField)
        }


GuildT = TypeVar("GuildT", bound=CogConfigBase)
ChannelT = TypeVar("ChannelT", bound=CogConfigBase)

class CogConfig(CogConfigBase, Generic[GuildT, ChannelT]):
    """
    Represents a cached copy of all cog configuration,
    with dynamically-defined type-hinted fields that may also be saved back to disk asynchronously.
    """
    _guild_type: type[GuildT]
    _channel_type: type[ChannelT]
    guild: dict[int, GuildT]
    channel: dict[int, ChannelT]

    def __init__(self, config: Config):
        self._config = config

    async def load_all(self, bot: Red):
        """Loads all cog configuration into memory."""
        self._init_fields(await self._config.all(), self._config)
        self.guild = {
            guild_id: self._guild_type(values, self._config.guild(guild))
            for guild_id, values in (await self._config.all_guilds()).items()
            if (guild := bot.get_guild(guild_id))
        }
        self.channel = {
            channel_id: self._channel_type(values, self._config.channel(channel))
            for channel_id, values in (await self._config.all_channels()).items()
            if (channel := bot.get_channel(channel_id))
            and isinstance(channel, (discord.TextChannel, discord.Thread))
        }

    async def load_guild(self, guild: discord.Guild) -> GuildT:
        """Loads a single guild config into memory."""
        if guild.id not in self.guild:
            self.guild[guild.id] = await self._guild_type.load(self._config.guild(guild))
        return self.guild[guild.id]
    
    async def load_channel(self, channel: discord.abc.Messageable) -> ChannelT:
        """Loads a single channel config into memory."""
        if not isinstance(channel, (discord.abc.GuildChannel, discord.Thread)):
            raise ValueError("Invalid channel for config")
        if channel.id not in self.channel:
            self.channel[channel.id] = await self._channel_type.load(self._config.channel(channel))
        return self.channel[channel.id]
    
    def register_all(self):
        """Registers the default values of all config groups in Red's config manager."""
        self._config.register_global(**self.defaults())
        self._config.register_guild(**self._guild_type.defaults())
        self._config.register_channel(**self._channel_type.defaults())

    @overload
    def __getitem__(self, key: discord.Guild | None) -> GuildT: ...

    @overload
    def __getitem__(self, key: discord.abc.Messageable | None) -> ChannelT: ...

    def __getitem__(self, key):
        if isinstance(key, discord.Guild):
            return self.guild[key.id]
        if isinstance(key, discord.abc.Messageable):
            return self.channel[getattr(key, "id")]
        raise TypeError(f"Invalid key {key}")
