from i18n import t as _t
import asyncio
import logging as py_logging
import os
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from aiogram import types, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramNotFound, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import BufferedInputFile
import keyboards as kb
import messages as bm
import messages.admin_messages as bm_admin
from app_context import bot, db
from config import ADMINS_UID, OUTPUT_DIR
from filters import IsBotAdmin
from services.logger import ERROR_LOG, EVENT_LOG, INFO_LOG, PERF_LOG, logger as logging
from services.download.queue import get_download_queue
from services.runtime.analytics_status import get_snapshot as get_analytics_runtime_snapshot
from services.runtime.stats import get_runtime_snapshot
logging = logging.bind(service='admin')
router = Router()
_ADMIN_ACCESS_REQUIRED = {'pt': 'Acesso administrativo necessário.', 'en': 'Admin access required.'}
_DOWNLOAD_CLEANUP_MIN_AGE_SECONDS = 6 * 60 * 60.0
_ADMIN_ACTIVE_CHECK_CONCURRENCY = 8
_ADMIN_MAILING_CONCURRENCY = 5
_ADMIN_THROTTLE_SECONDS = 0.05
_RUNTIME_LOG_FILES = (INFO_LOG, ERROR_LOG, EVENT_LOG, PERF_LOG)
_DOWNLOADABLE_LOG_FILES = (INFO_LOG, ERROR_LOG)

@dataclass(frozen=True, slots=True)
class _DirectorySnapshot:
    exists: bool
    file_count: int
    dir_count: int
    total_bytes: int
    oldest_file_age_seconds: float | None
    newest_file_age_seconds: float | None

def _is_admin_user(user_id: int | None) -> bool:
    return user_id is not None and int(user_id) in ADMINS_UID

async def _get_admin_lang(event) -> str:
    user_id = getattr(getattr(event, 'from_user', None), 'id', None)
    if user_id is None:
        return 'pt'
    try:
        return await db.get_language(user_id)
    except Exception:
        return 'pt'

async def _ensure_admin_callback(call: types.CallbackQuery) -> bool:
    if _is_admin_user(getattr(getattr(call, 'from_user', None), 'id', None)):
        return True
    await call.answer(_t('admin.access_required', await _get_admin_lang(call)), show_alert=True)
    return False

async def _ensure_admin_message(message: types.Message) -> bool:
    if _is_admin_user(getattr(getattr(message, 'from_user', None), 'id', None)):
        return True
    await message.answer(_t('admin.access_required', await _get_admin_lang(message)), reply_markup=types.ReplyKeyboardRemove())
    return False

async def _run_bounded(items, *, limit: int, worker):
    semaphore = asyncio.Semaphore(max(1, int(limit)))

    async def _wrapped(item):
        async with semaphore:
            return await worker(item)
    return await asyncio.gather(*(_wrapped(item) for item in items))

def _format_age(seconds: float | None, lang: str='pt') -> str:
    is_pt = str(lang).lower().startswith('pt')
    if seconds is None:
        return 'n/d' if is_pt else 'n/a'
    seconds = max(0, int(seconds))
    suffix = _t('handlers.admin._format_age.eb04b447', lang)
    if seconds < 60:
        return f'{seconds}s{suffix}'
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f'{minutes}m {sec}s{suffix}'
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f'{hours}h {minutes}m{suffix}'
    days, hours = divmod(hours, 24)
    return f'{days}d {hours}h{suffix}'

def _format_average_bytes(total_bytes: int, total_count: int) -> str:
    if total_count <= 0:
        return '0 B'
    return _format_bytes(int(total_bytes / total_count))

def _classify_perf_bottleneck(queue_wait_p95_ms: float, processing_p95_ms: float) -> str:
    if queue_wait_p95_ms > processing_p95_ms * 1.5:
        return 'queue-bound'
    if processing_p95_ms > queue_wait_p95_ms * 1.5:
        return 'download-bound'
    return 'balanced'

def _build_directory_snapshot(path_str: str, *, now: float | None=None) -> _DirectorySnapshot:
    root = Path(path_str)
    if not root.exists():
        return _DirectorySnapshot(False, 0, 0, 0, None, None)
    current_time = time.time() if now is None else now
    file_count = 0
    dir_count = 0
    total_bytes = 0
    oldest_mtime: float | None = None
    newest_mtime: float | None = None
    for current_root, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current_root)
        if current_path != root:
            dir_count += 1
        for dirname in list(dirnames):
            if (current_path / dirname).is_symlink():
                dirnames.remove(dirname)
        for filename in filenames:
            file_path = current_path / filename
            try:
                stat = file_path.stat()
            except OSError:
                continue
            file_count += 1
            total_bytes += max(0, int(stat.st_size))
            if oldest_mtime is None or stat.st_mtime < oldest_mtime:
                oldest_mtime = stat.st_mtime
            if newest_mtime is None or stat.st_mtime > newest_mtime:
                newest_mtime = stat.st_mtime
    return _DirectorySnapshot(exists=True, file_count=file_count, dir_count=dir_count, total_bytes=total_bytes, oldest_file_age_seconds=current_time - oldest_mtime if oldest_mtime is not None else None, newest_file_age_seconds=current_time - newest_mtime if newest_mtime is not None else None)

def _build_log_size_lines() -> list[str]:
    lines: list[str] = []
    for path_str in _RUNTIME_LOG_FILES:
        path = Path(path_str)
        size = path.stat().st_size if path.exists() else 0
        lines.append(f'{path.name}: {_format_bytes(size)}')
    return lines

async def _get_admin_counts() -> dict[str, int]:
    return await db.get_user_counts()

def _is_path_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False

def _cleanup_download_tree(*, now: float | None=None) -> tuple[int, int, int]:
    root = Path(OUTPUT_DIR).resolve()
    cutoff_timestamp = (time.time() if now is None else now) - _DOWNLOAD_CLEANUP_MIN_AGE_SECONDS
    removed_files = 0
    skipped_recent_files = 0
    candidate_dirs: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current_root).resolve()
        if not _is_path_within_root(current_path, root):
            dirnames[:] = []
            continue
        safe_dirnames: list[str] = []
        for dirname in dirnames:
            dir_path = current_path / dirname
            if dir_path.is_symlink():
                continue
            if _is_path_within_root(dir_path, root):
                safe_dirnames.append(dirname)
                candidate_dirs.append(dir_path.resolve())
        dirnames[:] = safe_dirnames
        for filename in filenames:
            file_path = current_path / filename
            if not _is_path_within_root(file_path, root):
                continue
            if file_path.stat().st_mtime > cutoff_timestamp:
                skipped_recent_files += 1
                continue
            file_path.unlink()
            removed_files += 1
    removed_dirs = 0
    for dir_path in sorted(candidate_dirs, key=lambda item: len(item.parts), reverse=True):
        if dir_path == root:
            continue
        try:
            next(dir_path.iterdir())
        except StopIteration:
            dir_path.rmdir()
            removed_dirs += 1
        except FileNotFoundError:
            continue
    return (removed_files, skipped_recent_files, removed_dirs)

async def _check_user_reachability(user) -> bool:
    user_id = int(user.user_id)
    user_status = user.status or 'inactive'
    send_successful = False
    try:
        await bot.send_chat_action(chat_id=user_id, action='typing')
        send_successful = True
    except TelegramRetryAfter as error:
        await asyncio.sleep(error.retry_after)
        try:
            await bot.send_chat_action(chat_id=user_id, action='typing')
            send_successful = True
        except TelegramRetryAfter as retry_error:
            logging.warning('Retry-after triggered twice while checking user %s: %s', user_id, retry_error)
        except (TelegramForbiddenError, TelegramNotFound, TelegramBadRequest) as retry_error:
            logging.info('User %s unreachable on retry: %s', user_id, retry_error)
        except TelegramAPIError as retry_error:
            logging.error('API error on retry while checking user %s: %s', user_id, retry_error)
        except Exception as retry_error:
            logging.error('Unexpected retry error while checking user %s: %s', user_id, retry_error)
    except (TelegramForbiddenError, TelegramNotFound, TelegramBadRequest) as error:
        logging.info('User %s unreachable: %s', user_id, error)
    except TelegramAPIError as error:
        logging.error('API error while checking user %s: %s', user_id, error)
    except Exception as error:
        logging.error('Unexpected error while checking user %s: %s', user_id, error)
    if send_successful:
        if user_status != 'active':
            await db.set_active(user_id)
    elif user_status != 'ban':
        await db.set_inactive(user_id)
    await asyncio.sleep(_ADMIN_THROTTLE_SECONDS)
    return send_successful

async def _deliver_mailing_message(user, *, sender_id: int, message_id: int) -> None:
    user_id = int(user.user_id)
    user_status = user.status or 'inactive'
    try:
        await bot.copy_message(chat_id=user_id, from_chat_id=sender_id, message_id=message_id)
        if user_status == 'inactive':
            await db.set_active(user_id)
    except TelegramForbiddenError as error:
        if "bots can't send messages to bots" in error.message:
            await db.delete_user(user_id)
        elif 'blocked' in error.message:
            await db.set_inactive(user_id)
    except TelegramBadRequest as error:
        if 'chat not found' in error.message.lower():
            await db.set_inactive(user_id)
    except Exception as error:
        logging.warning('Failed to deliver mailing message to %s: %s', user_id, error)
    finally:
        await asyncio.sleep(_ADMIN_THROTTLE_SECONDS)

class Mailing(StatesGroup):
    send_to_all_message = State()

class Admin(StatesGroup):
    write_message = State()
    write_chat_id = State()
    write_chat_text = State()

def _format_bytes(num_bytes: int) -> str:
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    value = float(max(0, num_bytes))
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == 'B':
                return f'{int(value)} {unit}'
            return f'{value:.2f} {unit}'
        value /= 1024.0
    return f'{value:.2f} TB'

def _render_session_text(lang: str='pt') -> str:
    is_pt = str(lang).lower().startswith('pt')
    snapshot = get_runtime_snapshot()
    uptime = str(timedelta(seconds=int(snapshot.uptime_seconds)))
    downloads_per_hour = snapshot.total_downloads / (snapshot.uptime_seconds / 3600.0) if snapshot.uptime_seconds > 0 and snapshot.total_downloads > 0 else 0.0
    if is_pt:
        lines = [_t('handlers.admin._render_session_text.14f1a46a', lang), _t('handlers.admin._render_session_text.83740664', lang, p0=uptime), _t('handlers.admin._render_session_text.63627479', lang, p0=snapshot.total_downloads, p1=f'{downloads_per_hour:.1f}'), _t('handlers.admin._render_session_text.22ffb113', lang, p0=snapshot.total_videos), _t('handlers.admin._render_session_text.ca9549b8', lang, p0=snapshot.total_audio), _t('handlers.admin._render_session_text.68b2f2a9', lang, p0=snapshot.total_other), _t('handlers.admin._render_session_text.4bb414c0', lang, p0=_format_bytes(snapshot.total_bytes)), _t('handlers.admin._render_session_text.d4208cbc', lang, p0=_format_average_bytes(snapshot.total_bytes, snapshot.total_downloads)), _t('handlers.admin._render_session_text.45d43d42', lang, p0=_format_age(time.monotonic() - snapshot.last_download_monotonic if snapshot.last_download_monotonic is not None else None, lang))]
    else:
        lines = [_t('handlers.admin._render_session_text.14f1a46a', lang), _t('handlers.admin._render_session_text.83740664', lang, p0=uptime), _t('handlers.admin._render_session_text.63627479', lang, p0=snapshot.total_downloads, p1=f'{downloads_per_hour:.1f}'), _t('handlers.admin._render_session_text.22ffb113', lang, p0=snapshot.total_videos), _t('handlers.admin._render_session_text.ca9549b8', lang, p0=snapshot.total_audio), _t('handlers.admin._render_session_text.68b2f2a9', lang, p0=snapshot.total_other), _t('handlers.admin._render_session_text.4bb414c0', lang, p0=_format_bytes(snapshot.total_bytes)), _t('handlers.admin._render_session_text.d4208cbc', lang, p0=_format_average_bytes(snapshot.total_bytes, snapshot.total_downloads)), _t('handlers.admin._render_session_text.45d43d42', lang, p0=_format_age(time.monotonic() - snapshot.last_download_monotonic if snapshot.last_download_monotonic is not None else None, lang))]
    if snapshot.by_source:
        lines.extend(['', _t('handlers.admin._render_session_text.5e939b43', lang)])
        for source, payload in sorted(snapshot.by_source.items(), key=lambda item: (-int(item[1].get('count', 0) or 0), item[0])):
            count = int(payload.get('count', 0) or 0)
            size_bytes = int(payload.get('bytes', 0) or 0)
            share = count / snapshot.total_downloads * 100.0 if snapshot.total_downloads else 0.0
            avg_label = _t('handlers.admin._render_session_text.ca5c8585', lang)
            lines.append(f'{source}: {count} ({share:.0f}%) | {_format_bytes(size_bytes)} | {avg_label} {_format_average_bytes(size_bytes, count)}')
    return '\n'.join(lines)

async def _render_perf_text(*, include_load: bool=True, lang: str='pt') -> str:
    is_pt = str(lang).lower().startswith('pt')
    queue = get_download_queue()
    snapshot = await queue.metrics_snapshot()
    if not snapshot:
        return bm.no_queue_metrics_yet(lang=lang)
    lines = [_t('handlers.admin._render_perf_text.3f2bd673', lang)]
    if include_load:
        load = queue.load_snapshot()
        if is_pt:
            lines.extend([_t('handlers.admin._render_perf_text.ac9f2c78', lang, p0=load.queued_jobs), _t('handlers.admin._render_perf_text.e172193f', lang, p0=load.active_jobs), _t('handlers.admin._render_perf_text.460f7372', lang, p0=load.active_workers)])
        else:
            lines.extend([_t('handlers.admin._render_perf_text.ac9f2c78', lang, p0=load.queued_jobs), _t('handlers.admin._render_perf_text.e172193f', lang, p0=load.active_jobs), _t('handlers.admin._render_perf_text.460f7372', lang, p0=load.active_workers)])
    try:
        cache_stats = db.get_file_cache_stats()
        if is_pt:
            lines.append(_t('handlers.admin._render_perf_text.f362761a', lang, p0=cache_stats['hits'], p1=cache_stats['misses'], p2=f"{cache_stats['hit_rate_pct']:.1f}"))
        else:
            lines.append(_t('handlers.admin._render_perf_text.f362761a', lang, p0=cache_stats['hits'], p1=cache_stats['misses'], p2=f"{cache_stats['hit_rate_pct']:.1f}"))
    except Exception:
        pass
    lines.append(_t('handlers.admin._render_perf_text.bcf82d18', lang, p0=len(snapshot)))
    sorted_sources = sorted(snapshot.items(), key=lambda item: max(item[1].queue_wait_p95_ms, item[1].processing_p95_ms), reverse=True)
    bottleneck_pt = {'queue-bound': _t('handlers.admin._render_perf_text.8b8340a7', lang), 'download-bound': _t('handlers.admin._render_perf_text.0e7b7190', lang), 'balanced': _t('admin.bound.balanced', lang)}
    for source, item in sorted_sources:
        bottleneck = _classify_perf_bottleneck(item.queue_wait_p95_ms, item.processing_p95_ms)
        if is_pt:
            lines.append(_t('handlers.admin._render_perf_text.73b1aab0', lang, p0=source, p1=item.count, p2=f'{item.queue_wait_p50_ms:.0f}', p3=f'{item.queue_wait_p95_ms:.0f}', p4=f'{item.processing_p50_ms:.0f}', p5=f'{item.processing_p95_ms:.0f}', p6=bottleneck_pt.get(bottleneck, bottleneck)))
        else:
            lines.append(_t('handlers.admin._render_perf_text.73b1aab0', lang, p0=source, p1=item.count, p2=f'{item.queue_wait_p50_ms:.0f}', p3=f'{item.queue_wait_p95_ms:.0f}', p4=f'{item.processing_p50_ms:.0f}', p5=f'{item.processing_p95_ms:.0f}', p6=bottleneck_pt.get(bottleneck, bottleneck)))
    return '\n'.join(lines)

async def _render_health_text(*, include_queue: bool=True, include_runtime_downloads: bool=True, lang: str='pt') -> str:
    is_pt = str(lang).lower().startswith('pt')
    queue_snapshot = get_download_queue().load_snapshot()
    runtime_snapshot = get_runtime_snapshot()
    analytics_snapshot = get_analytics_runtime_snapshot()
    uptime = str(timedelta(seconds=int(runtime_snapshot.uptime_seconds)))
    if is_pt:
        lines = [_t('handlers.admin._render_health_text.7d27a2e7', lang), _t('handlers.admin._render_session_text.83740664', lang, p0=uptime), _t('handlers.admin._render_session_text.45d43d42', lang, p0=_format_age(time.monotonic() - runtime_snapshot.last_download_monotonic if runtime_snapshot.last_download_monotonic is not None else None, lang)), _t('handlers.admin._render_health_text.22456ec5', lang, p0=analytics_snapshot.dropped_events), _t('handlers.admin._render_health_text.5e1c2135', lang, p0=_format_age(time.monotonic() - analytics_snapshot.last_drop_monotonic if analytics_snapshot.last_drop_monotonic is not None else None, lang)), '', _t('handlers.admin._render_health_text.51eaa53a', lang), *_build_log_size_lines()]
    else:
        lines = [_t('handlers.admin._render_health_text.7d27a2e7', lang), _t('handlers.admin._render_session_text.83740664', lang, p0=uptime), _t('handlers.admin._render_session_text.45d43d42', lang, p0=_format_age(time.monotonic() - runtime_snapshot.last_download_monotonic if runtime_snapshot.last_download_monotonic is not None else None, lang)), _t('handlers.admin._render_health_text.22456ec5', lang, p0=analytics_snapshot.dropped_events), _t('handlers.admin._render_health_text.5e1c2135', lang, p0=_format_age(time.monotonic() - analytics_snapshot.last_drop_monotonic if analytics_snapshot.last_drop_monotonic is not None else None, lang)), '', _t('handlers.admin._render_health_text.51eaa53a', lang), *_build_log_size_lines()]
    if include_runtime_downloads:
        lines.insert(2, _t('handlers.admin._render_health_text.044f58fa', lang, p0=runtime_snapshot.total_downloads))
    if include_queue:
        if is_pt:
            queue_line = _t('handlers.admin._render_health_text.0ab68c6d', lang, p0=queue_snapshot.queued_jobs, p1=queue_snapshot.active_jobs, p2=queue_snapshot.active_workers)
        else:
            queue_line = _t('handlers.admin._render_health_text.0ab68c6d', lang, p0=queue_snapshot.queued_jobs, p1=queue_snapshot.active_jobs, p2=queue_snapshot.active_workers)
        lines.insert(2, queue_line)
    return '\n'.join(lines)

async def _render_downloads_text(*, footer: str | None=None, include_cleanup_load: bool=True, lang: str='pt') -> str:
    is_pt = str(lang).lower().startswith('pt')
    queue_snapshot = get_download_queue().load_snapshot()
    snapshot = await asyncio.to_thread(_build_directory_snapshot, OUTPUT_DIR)
    cleanup_ready = queue_snapshot.active_jobs == 0 and queue_snapshot.queued_jobs == 0
    yes = _t('handlers.admin._render_downloads_text.8a798890', lang)
    no = _t('handlers.admin._render_downloads_text.76daee16', lang) if is_pt else 'no'
    if is_pt:
        lines = [_t('handlers.admin._render_downloads_text.b2c5bf40', lang), _t('handlers.admin._render_downloads_text.14679791', lang, p0=OUTPUT_DIR), _t('handlers.admin._render_downloads_text.bc90fe3a', lang, p0=yes if snapshot.exists else no), _t('handlers.admin._render_downloads_text.7757f2b4', lang, p0=snapshot.file_count), _t('handlers.admin._render_downloads_text.a40e0507', lang, p0=snapshot.dir_count), _t('handlers.admin._render_downloads_text.5efcbc66', lang, p0=_format_bytes(snapshot.total_bytes)), _t('handlers.admin._render_downloads_text.d05a4609', lang, p0=_format_age(snapshot.oldest_file_age_seconds, lang)), _t('handlers.admin._render_downloads_text.96876281', lang, p0=_format_age(snapshot.newest_file_age_seconds, lang)), '', _t('handlers.admin._render_downloads_text.c7489a74', lang), _t('handlers.admin._render_downloads_text.254080f9', lang, p0=yes if cleanup_ready else no)]
    else:
        lines = [_t('handlers.admin._render_downloads_text.b2c5bf40', lang), _t('handlers.admin._render_downloads_text.14679791', lang, p0=OUTPUT_DIR), _t('handlers.admin._render_downloads_text.bc90fe3a', lang, p0=yes if snapshot.exists else no), _t('handlers.admin._render_downloads_text.7757f2b4', lang, p0=snapshot.file_count), _t('handlers.admin._render_downloads_text.a40e0507', lang, p0=snapshot.dir_count), _t('handlers.admin._render_downloads_text.5efcbc66', lang, p0=_format_bytes(snapshot.total_bytes)), _t('handlers.admin._render_downloads_text.d05a4609', lang, p0=_format_age(snapshot.oldest_file_age_seconds, lang)), _t('handlers.admin._render_downloads_text.96876281', lang, p0=_format_age(snapshot.newest_file_age_seconds, lang)), '', _t('handlers.admin._render_downloads_text.c7489a74', lang), _t('handlers.admin._render_downloads_text.254080f9', lang, p0=yes if cleanup_ready else no)]
    if include_cleanup_load:
        if is_pt:
            queue_line = _t('handlers.admin._render_downloads_text.9a394b1f', lang, p0=queue_snapshot.queued_jobs, p1=queue_snapshot.active_jobs)
        else:
            queue_line = _t('handlers.admin._render_downloads_text.9a394b1f', lang, p0=queue_snapshot.queued_jobs, p1=queue_snapshot.active_jobs)
        lines.insert(-1, queue_line)
    if footer:
        lines.extend(['', footer])
    return '\n'.join(lines)

async def _render_ops_text(lang: str='pt') -> str:
    health_text = await _render_health_text(include_queue=False, include_runtime_downloads=False, lang=lang)
    perf_text = await _render_perf_text(lang=lang)
    return f'{health_text}\n\n{perf_text}'

async def _render_runtime_storage_text(*, footer: str | None=None, lang: str='pt') -> str:
    session_text = _render_session_text(lang=lang)
    downloads_text = await _render_downloads_text(footer=footer, include_cleanup_load=False, lang=lang)
    return f'{session_text}\n\n{downloads_text}'

async def _render_admin_panel(message: types.Message, *, edit: bool, lang: str | None=None) -> None:
    if lang is None:
        user_id = getattr(getattr(message, 'from_user', None), 'id', None)
        lang = await db.get_language(user_id) if user_id is not None else 'pt'
    counts = await _get_admin_counts()
    queue_snapshot = get_download_queue().load_snapshot()
    runtime_snapshot = get_runtime_snapshot()
    runtime_title = _t('handlers.admin._render_admin_panel.1ca3fe3a', lang)
    queue_label = _t('handlers.admin._render_admin_panel.3b2fe03e', lang)
    queued_label = _t('handlers.admin._render_admin_panel.d36be649', lang)
    active_label = _t('handlers.admin._render_admin_panel.96879611', lang)
    downloads_label = _t('handlers.admin._render_admin_panel.a0b7e373', lang)
    panel_text = bm.admin_panel(counts['user_count'], counts['private_chat_count'], counts['group_chat_count'], counts['active_user_count'], counts['inactive_user_count'], lang=lang)
    text = _t('handlers.admin._render_admin_panel.85f37ec1', lang, p0=panel_text, p1=runtime_title, p2=queue_label, p3=queue_snapshot.queued_jobs, p4=queued_label, p5=queue_snapshot.active_jobs, p6=active_label, p7=queue_snapshot.active_workers, p8=downloads_label, p9=runtime_snapshot.total_downloads)
    markup = kb.admin_keyboard(lang=lang)
    if edit:
        await message.edit_text(text=text, reply_markup=markup, parse_mode='HTML')
    else:
        await message.answer(text=text, reply_markup=markup, parse_mode='HTML')

async def _cleanup_downloads_once(lang: str = "pt") -> str:
    queue_snapshot = get_download_queue().load_snapshot()
    if queue_snapshot.active_jobs > 0 or queue_snapshot.queued_jobs > 0:
        return bm.downloads_cleanup_blocked(queue_snapshot.active_jobs, queue_snapshot.queued_jobs, lang=lang)
    if not os.path.exists(OUTPUT_DIR):
        return (
            _t('handlers.admin._cleanup_downloads_once.f00056e1', lang, p0=OUTPUT_DIR)
        )
    removed_files, skipped_recent_files, removed_dirs = await asyncio.to_thread(_cleanup_download_tree)
    return bm.downloads_cleanup_finished(removed_files, removed_dirs, skipped_recent_files, lang=lang)

@router.message(Command('admin'), IsBotAdmin())
async def admin(message: types.Message):
    user_lang = await _get_admin_lang(message)
    await bot.send_chat_action(message.chat.id, 'typing')
    if message.chat.type == 'private':
        await _render_admin_panel(message, edit=False, lang=user_lang)
    else:
        await message.answer(bm.not_groups(lang=user_lang))

@router.message(Command('perf'), IsBotAdmin())
async def perf_metrics(message: types.Message):
    user_lang = await _get_admin_lang(message)
    await message.answer(await _render_perf_text(lang=user_lang), parse_mode='HTML')

@router.message(Command('session'), IsBotAdmin())
async def session_metrics(message: types.Message):
    user_lang = await _get_admin_lang(message)
    await message.answer(_render_session_text(lang=user_lang), parse_mode='HTML')

@router.callback_query(F.data == 'admin_refresh')
async def admin_refresh(call: types.CallbackQuery):
    user_lang = await _get_admin_lang(call)
    if not await _ensure_admin_callback(call):
        return
    await call.answer()
    await _render_admin_panel(call.message, edit=True, lang=user_lang)

@router.callback_query(F.data == 'admin_ops')
async def admin_ops(call: types.CallbackQuery):
    user_lang = await _get_admin_lang(call)
    if not await _ensure_admin_callback(call):
        return
    await call.answer()
    await call.message.edit_text(text=await _render_ops_text(lang=user_lang), reply_markup=kb.admin_detail_keyboard('admin_ops', lang=user_lang), parse_mode='HTML')

@router.callback_query(F.data == 'admin_runtime_storage')
async def admin_runtime_storage(call: types.CallbackQuery):
    user_lang = await _get_admin_lang(call)
    if not await _ensure_admin_callback(call):
        return
    await call.answer()
    queue_snapshot = get_download_queue().load_snapshot()
    await call.message.edit_text(text=await _render_runtime_storage_text(lang=user_lang), reply_markup=kb.downloads_admin_keyboard(can_cleanup=queue_snapshot.active_jobs == 0 and queue_snapshot.queued_jobs == 0, refresh_callback='admin_runtime_storage', lang=user_lang), parse_mode='HTML')

@router.callback_query(F.data == 'admin_health')
async def admin_health(call: types.CallbackQuery):
    user_lang = await _get_admin_lang(call)
    if not await _ensure_admin_callback(call):
        return
    await call.answer()
    await call.message.edit_text(text=await _render_health_text(lang=user_lang), reply_markup=kb.return_back_to_admin_keyboard(lang=user_lang), parse_mode='HTML')

@router.callback_query(F.data == 'admin_session')
async def admin_session(call: types.CallbackQuery):
    user_lang = await _get_admin_lang(call)
    if not await _ensure_admin_callback(call):
        return
    await call.answer()
    await call.message.edit_text(text=_render_session_text(lang=user_lang), reply_markup=kb.return_back_to_admin_keyboard(lang=user_lang), parse_mode='HTML')

@router.callback_query(F.data == 'admin_perf')
async def admin_perf(call: types.CallbackQuery):
    user_lang = await _get_admin_lang(call)
    if not await _ensure_admin_callback(call):
        return
    await call.answer()
    await call.message.edit_text(text=await _render_perf_text(lang=user_lang), reply_markup=kb.return_back_to_admin_keyboard(lang=user_lang), parse_mode='HTML')

@router.callback_query(F.data == 'admin_downloads')
async def admin_downloads(call: types.CallbackQuery):
    user_lang = await _get_admin_lang(call)
    if not await _ensure_admin_callback(call):
        return
    await call.answer()
    queue_snapshot = get_download_queue().load_snapshot()
    await call.message.edit_text(text=await _render_downloads_text(lang=user_lang), reply_markup=kb.downloads_admin_keyboard(can_cleanup=queue_snapshot.active_jobs == 0 and queue_snapshot.queued_jobs == 0, lang=user_lang), parse_mode='HTML')

@router.callback_query(F.data == 'admin_cleanup_downloads')
async def admin_cleanup_downloads(call: types.CallbackQuery):
    user_lang = await _get_admin_lang(call)
    if not await _ensure_admin_callback(call):
        return
    status_text = await _cleanup_downloads_once(lang=user_lang)
    await call.answer(
        _t('handlers.admin.admin_cleanup_downloads.03c5eef4', None)
            if any(word in status_text.lower() for word in ('concluída', 'finished', 'terminada'))
            else _t('handlers.admin.admin_cleanup_downloads.73297ab5', None)
    )
    queue_snapshot = get_download_queue().load_snapshot()
    await call.message.edit_text(text=await _render_runtime_storage_text(footer=status_text, lang=user_lang), reply_markup=kb.downloads_admin_keyboard(can_cleanup=queue_snapshot.active_jobs == 0 and queue_snapshot.queued_jobs == 0, refresh_callback='admin_runtime_storage', lang=user_lang), parse_mode='HTML')

@router.callback_query(F.data == 'delete_log')
async def del_log(call: types.CallbackQuery):
    user_lang = await _get_admin_lang(call)
    if not await _ensure_admin_callback(call):
        return
    await bot.send_chat_action(call.message.chat.id, 'typing')
    py_logging.shutdown()
    for log_path in _RUNTIME_LOG_FILES:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('', encoding='utf-8')
    await call.message.reply(bm.log_deleted(lang=user_lang))
    await call.answer()

@router.callback_query(F.data == 'download_log')
async def download_log_handler(call: types.CallbackQuery):
    if not await _ensure_admin_callback(call):
        return
    await bot.send_chat_action(call.message.chat.id, 'typing')
    user_id = call.from_user.id
    await call.answer()
    for file_path in _DOWNLOADABLE_LOG_FILES:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        with path.open('rb') as file:
            await call.message.answer_document(BufferedInputFile(file.read(), filename=path.name))
    logging.info('User action: Downloaded logs (User ID: %s)', user_id)

@router.callback_query(F.data == 'check_active_users')
async def check_active_users(call: types.CallbackQuery):
    user_lang = await _get_admin_lang(call)
    if not await _ensure_admin_callback(call):
        return
    await call.answer()
    await bot.send_chat_action(call.message.chat.id, 'typing')
    users_info = await db.get_all_users_info()
    users_to_check = [user for user in users_info if (user.status or 'inactive') != 'ban']
    if not users_to_check:
        await call.message.edit_text(bm.active_users_check_no_targets(lang=user_lang), reply_markup=kb.return_back_to_admin_keyboard(lang=user_lang))
        return
    total_users = len(users_to_check)
    status_message = await call.message.edit_text(bm.active_users_check_started(total_users, lang=user_lang))
    results = await _run_bounded(users_to_check, limit=_ADMIN_ACTIVE_CHECK_CONCURRENCY, worker=_check_user_reachability)
    reachable = sum((1 for result in results if result))
    unreachable = total_users - reachable
    await status_message.edit_text(bm.active_users_check_completed(total_users, reachable, unreachable, lang=user_lang), reply_markup=kb.return_back_to_admin_keyboard(lang=user_lang), parse_mode='HTML')

@router.callback_query(F.data == 'cancel_action')
async def cancel_action(call: types.CallbackQuery, state: FSMContext):
    user_lang = await _get_admin_lang(call)
    if not await _ensure_admin_callback(call):
        return
    current_state = await state.get_state()
    if current_state is None:
        await call.answer(
        _t('handlers.admin.cancel_action.0380268e', None)
    )
        return
    await state.clear()
    await call.answer(
        _t('handlers.admin.cancel_action.13ca2ee2', None)
    )
    try:
        await call.message.edit_text(bm.canceled(lang=user_lang), reply_markup=kb.return_back_to_admin_keyboard(lang=user_lang))
    except TelegramBadRequest:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await call.message.answer(bm.canceled(lang=user_lang), reply_markup=kb.return_back_to_admin_keyboard(lang=user_lang))

@router.callback_query(F.data == 'message_chat_id')
async def message_chat_id(call: types.CallbackQuery, state: FSMContext):
    user_lang = await _get_admin_lang(call)
    if not await _ensure_admin_callback(call):
        return
    await call.answer()
    await bot.send_chat_action(call.message.chat.id, 'typing')
    await call.message.edit_text(bm.enter_chat_id(lang=user_lang), reply_markup=kb.cancel_keyboard(lang=user_lang))
    await state.set_state(Admin.write_chat_id)

@router.message(Admin.write_chat_id)
async def admin_collect_chat_id(message: types.Message, state: FSMContext):
    user_lang = await _get_admin_lang(message)
    if not await _ensure_admin_message(message):
        await state.clear()
        return
    if message.text == bm.cancel(lang=user_lang):
        await bot.send_message(message.chat.id, bm.canceled(lang=user_lang), reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return
    chat_id_text = message.text.strip()
    try:
        chat_id = int(chat_id_text)
    except ValueError:
        await message.reply(bm.invalid_chat_id(lang=user_lang))
        return
    await state.update_data(target_chat_id=chat_id)
    known_info = await db.get_user_info(chat_id)
    preview_lines: list[str] = []
    if known_info:
        preview_lines.append(bm.known_chat_target(chat_id, known_info[0], known_info[1], known_info[2], lang=user_lang))
    else:
        preview_lines.append(bm.unknown_chat_target(chat_id, lang=user_lang))
    preview_lines.append('')
    preview_lines.append(bm.enter_chat_message(lang=user_lang))
    await message.answer('\n'.join(preview_lines), reply_markup=kb.cancel_keyboard(lang=user_lang), parse_mode='HTML')
    await state.set_state(Admin.write_chat_text)

@router.message(Admin.write_chat_text)
async def admin_send_to_chat(message: types.Message, state: FSMContext):
    user_lang = await _get_admin_lang(message)
    if not await _ensure_admin_message(message):
        await state.clear()
        return
    if message.text == bm.cancel(lang=user_lang):
        await bot.send_message(message.chat.id, bm.canceled(lang=user_lang), reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return
    data = await state.get_data()
    chat_id = data.get('target_chat_id')
    if chat_id is None:
        await message.reply(bm.chat_message_failed('unknown', lang=user_lang), reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return
    chat_text = message.text
    await state.clear()
    progress_message = await message.reply(bm.chat_message_sending(lang=user_lang), reply_markup=types.ReplyKeyboardRemove())
    sent_message = None
    try:
        sent_message = await bot.send_message(chat_id=chat_id, text=chat_text)
    except (TelegramForbiddenError, TelegramNotFound, TelegramBadRequest) as error:
        logging.info('Failed to send message to chat %s: %s', chat_id, error)
        await bot.delete_message(message.chat.id, progress_message.message_id)
        await message.answer(bm.chat_message_failed(chat_id, lang=user_lang), reply_markup=kb.return_back_to_admin_keyboard(lang=user_lang))
        return
    except TelegramAPIError as error:
        logging.error('API error while sending message to chat %s: %s', chat_id, error)
        await bot.delete_message(message.chat.id, progress_message.message_id)
        await message.answer(bm.chat_message_failed(chat_id, lang=user_lang), reply_markup=kb.return_back_to_admin_keyboard(lang=user_lang))
        return
    except Exception as error:
        logging.error('Unexpected error while sending message to chat %s: %s', chat_id, error)
        await bot.delete_message(message.chat.id, progress_message.message_id)
        await message.answer(bm.chat_message_failed(chat_id, lang=user_lang), reply_markup=kb.return_back_to_admin_keyboard(lang=user_lang))
        return
    if sent_message:
        chat_obj = sent_message.chat
        chat_type_raw = getattr(chat_obj, 'type', None)
        chat_type_str = chat_type_raw.value if hasattr(chat_type_raw, 'value') else str(chat_type_raw)
        chat_type_value = 'private' if chat_type_str == 'private' else 'public'
        chat_name = getattr(chat_obj, 'title', None) or getattr(chat_obj, 'full_name', None)
        if not chat_name:
            first_name = getattr(chat_obj, 'first_name', None)
            last_name = getattr(chat_obj, 'last_name', None)
            name_parts = [part for part in (first_name, last_name) if part]
            if name_parts:
                chat_name = ' '.join(name_parts)
        if not chat_name:
            chat_name = _t('handlers.admin.admin_send_to_chat.1744850b', None, p0=chat_id)
        chat_username = getattr(chat_obj, 'username', None)
        language_code = getattr(chat_obj, 'language_code', None)
        await db.upsert_chat(user_id=chat_id, user_name=chat_name, user_username=chat_username, chat_type=chat_type_value, language=language_code, status='active')
    await bot.delete_message(message.chat.id, progress_message.message_id)
    await message.answer(bm.chat_message_sent(chat_id, lang=user_lang), reply_markup=kb.return_back_to_admin_keyboard(lang=user_lang))

@router.callback_query(F.data == 'back_to_admin')
async def back_to_admin(call: types.CallbackQuery):
    user_lang = await _get_admin_lang(call)
    if not await _ensure_admin_callback(call):
        return
    await call.answer()
    await _render_admin_panel(call.message, edit=True, lang=user_lang)

@router.callback_query(F.data == 'send_to_all')
async def send_to_all_callback(call: types.CallbackQuery, state: FSMContext):
    user_lang = await _get_admin_lang(call)
    if not await _ensure_admin_callback(call):
        return
    users = await db.get_all_users_info()
    active_users = sum((1 for user in users if (user.status or 'inactive') == 'active'))
    banned_users = sum((1 for user in users if (user.status or 'inactive') == 'ban'))
    private_users = sum((1 for user in users if user.chat_type == 'private'))
    group_users = sum((1 for user in users if user.chat_type != 'private'))
    await call.message.edit_text(text=bm.mailing_audience_preview(len(users) - banned_users, active_users, len(users) - active_users - banned_users, banned_users, private_users, group_users, lang=user_lang), reply_markup=kb.cancel_keyboard(lang=user_lang), parse_mode='HTML')
    await state.set_state(Mailing.send_to_all_message)
    await call.answer()

@router.message(Mailing.send_to_all_message)
async def send_to_all_message(message: types.Message, state: FSMContext):
    user_lang = await _get_admin_lang(message)
    if not await _ensure_admin_message(message):
        await state.clear()
        return
    sender_id = message.from_user.id
    if message.text == bm.cancel(lang=user_lang):
        await message.answer(text=bm.canceled(lang=user_lang), reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return
    else:
        await state.clear()
        await bot.send_message(chat_id=message.chat.id, text=bm.start_mailing(lang=user_lang), reply_markup=types.ReplyKeyboardRemove())
        users = [user for user in await db.get_all_users_info() if (user.status or 'inactive') != 'ban']
        await _run_bounded(users, limit=_ADMIN_MAILING_CONCURRENCY, worker=lambda user: _deliver_mailing_message(user, sender_id=sender_id, message_id=message.message_id))
        await bot.send_message(chat_id=message.chat.id, text=bm.finish_mailing(lang=user_lang), reply_markup=types.ReplyKeyboardRemove())
        return

@router.callback_query(F.data.startswith('write_'))
async def write_message_handler(call: types.CallbackQuery, state: FSMContext):
    user_lang = await _get_admin_lang(call)
    if not await _ensure_admin_callback(call):
        return
    chat_id = call.data.split('_')[1]
    await call.message.delete_reply_markup()
    await call.message.delete()
    await call.message.answer(bm.please_type_message(lang=user_lang), reply_markup=kb.cancel_keyboard(lang=user_lang))
    await state.set_state(Admin.write_message)
    await state.update_data(chat_id=chat_id)

@router.message(Admin.write_message)
async def write_message(message: types.Message, state: FSMContext):
    user_lang = await _get_admin_lang(message)
    if not await _ensure_admin_message(message):
        await state.clear()
        return
    answer = message.text
    if answer == bm.cancel(lang=user_lang):
        await bot.send_message(message.chat.id, bm.canceled(lang=user_lang), reply_markup=types.ReplyKeyboardRemove())
        await state.clear()
        return
    data = await state.get_data()
    chat_id = data.get('chat_id')
    await state.clear()
    try:
        await bot.send_message(chat_id=chat_id, text=answer)
        message_sent = await message.reply(bm.your_message_sent(lang=user_lang), reply_markup=types.ReplyKeyboardRemove())
        await bot.delete_message(message.chat.id, message_sent.message_id)
        await message.answer(bm.your_message_sent(lang=user_lang), reply_markup=kb.return_back_to_admin_keyboard(lang=user_lang))
    except Exception as e:
        logging.error(e)
        await message.reply(bm_admin.something_went_wrong(), reply_markup=kb.return_back_to_admin_keyboard(lang=user_lang))

async def clear_downloads_and_notify():
    try:
        message = await _cleanup_downloads_once(lang='en')
    except Exception as e:
        message = _t('handlers.admin.clear_downloads_and_notify.51d62c73', None, p0=e)
    try:
        deleted_files = await db.cleanup_expired_files()
        if deleted_files > 0:
            logging.event('file_cache_cleanup', deleted=deleted_files)
            message += _t('handlers.admin.clear_downloads_and_notify.8d5fe0cc', None, p0=deleted_files)
    except Exception as e:
        logging.error('Failed to clean up expired file cache entries: %s', e)
    for admin_id in ADMINS_UID:
        try:
            await bot.send_message(chat_id=admin_id, text=message)
        except Exception as e:
            logging.error('Failed to send a message to admin %s: %s', admin_id, e)
