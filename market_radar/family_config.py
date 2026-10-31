"""Explicit family-only path binding; imported before the collector modules."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import time
import uuid


class FamilyBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class FamilyConfig:
    project_root: Path
    data_root: Path
    cache: Path
    normal_shutdown: bool = False
    emergency_shutdown: bool = False
    expected_windows_timezone: str = 'Korea Standard Time'
    minimum_free_gib: float = 5

    @property
    def env(self):
        return self.project_root / '.env'

    @classmethod
    def load(cls, path, *, dry_run=False):
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict) or data.get('config_version') != 1:
                raise ValueError()
            paths = [Path(data[k]) for k in ('project_root', 'data_root', 'cache')]
            if not all(p.is_absolute() for p in paths):
                raise ValueError()
            for key in ('normal_shutdown', 'emergency_shutdown'):
                if type(data.get(key, False)) is not bool:
                    raise ValueError()
            return cls(*(p.resolve() for p in paths),
                normal_shutdown=data.get('normal_shutdown', False) and not dry_run,
                emergency_shutdown=data.get('emergency_shutdown', False) and not dry_run,
                expected_windows_timezone=data.get('expected_windows_timezone', 'Korea Standard Time'))
        except (OSError, ValueError, KeyError, TypeError):
            raise FamilyBlocked('family_config_invalid') from None

    def bind_runtime(self):
        import runtime_paths
        runtime_paths.APP_ROOT = self.project_root

    def inspect_paths(self, *, write_probe=True):
        from dotenv import dotenv_values
        if not self.project_root.is_dir() or not self.data_root.is_dir():
            raise FamilyBlocked('data_or_project_root_missing')
        if not self.env.is_file():
            raise FamilyBlocked('credentials_file_missing')
        try:
            # Same loader and keys as KiwoomRest; never persist or display values.
            values = dotenv_values(self.env)
            if values.get('KIWOOM_MODE') != 'real' or not all(values.get(k) for k in ('APP_KEY', 'APP_SECRET')):
                raise FamilyBlocked('credentials_missing_or_wrong_mode')
            del values
            if not self.cache.is_file() or self.cache.is_symlink():
                raise FamilyBlocked('shared_cache_missing')
            # No sqlite connection/checkpoint, no 5+ GB cache copy.
            with self.cache.open('r+b') as stream:
                if stream.read(16) != b'SQLite format 3\x00':
                    raise FamilyBlocked('shared_cache_header_invalid')
            free = shutil.disk_usage(self.data_root).free
            if free < self.minimum_free_gib * 1024**3:
                raise FamilyBlocked('insufficient_disk_space')
            if write_probe:
                probe = self.data_root / ('.family_probe_' + uuid.uuid4().hex)
                try:
                    with probe.open('xb') as stream:
                        stream.write(b'permission probe')
                        stream.flush()
                        os.fsync(stream.fileno())
                finally:
                    probe.unlink(missing_ok=True)
        except OSError:
            raise FamilyBlocked('path_access_failed') from None
        return dict(env_path=str(self.env), credentials_present=True, authentication='NOT_TESTED',
            data_root=str(self.data_root), shared_cache=str(self.cache), cache_bytes=self.cache.stat().st_size,
            cache_access='read_write_open_without_modification', free_bytes=free)


def windows_clock_check(expected_timezone):
    import ctypes
    import winreg
    if os.name != 'nt':
        raise FamilyBlocked('windows_required')
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r'SYSTEM\CurrentControlSet\Control\TimeZoneInformation') as key:
            zone = winreg.QueryValueEx(key, 'TimeZoneKeyName')[0].rstrip('\x00')
        if zone != expected_timezone:
            raise FamilyBlocked('windows_timezone_mismatch')
        wall, mono = time.time(), time.monotonic()
        time.sleep(.15)
        difference = abs((time.time()-wall) - (time.monotonic()-mono))
        if difference > .1:
            raise FamilyBlocked('unstable_system_clock')
        return dict(windows_timezone=zone, local_time=datetime.now().astimezone().isoformat(),
            utc=datetime.now(timezone.utc).isoformat(), observation_drift_seconds=difference,
            absolute_utc_verified=False, note='Local clock consistency only; no NTP authentication')
    except OSError:
        raise FamilyBlocked('windows_clock_check_unavailable') from None
