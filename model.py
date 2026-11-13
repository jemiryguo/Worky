import copy
import hashlib
import json
import math
import os
import tempfile
from uuid import uuid4
import fcntl
from contextlib import contextmanager
from datetime import datetime, date, time
from pathlib import Path
from zoneinfo import ZoneInfo

STATUSES = ('进行中', '已经完结', '已经废弃')
PRIORITIES = ('', 'P0', 'P1', 'P2', 'P3')
LABEL_COLORS = ('blue', 'green', 'purple', 'amber', 'rose', 'teal', 'indigo', 'olive', 'coral', 'cyan', 'mauve', 'sand', 'mint', 'lavender', 'peach', 'sage', 'plum', 'sky', 'lemon', 'salmon', 'slate', 'taupe', 'seafoam', 'orchid')
TZ = ZoneInfo(os.environ.get('TASK_TIMEZONE', 'Asia/Shanghai'))


def today():
    return datetime.now(TZ).date()


def date_moment(day, end=False):
    if day == today():
        return datetime.now(TZ).isoformat()
    return datetime.combine(day, time.max if end else time.min, tzinfo=TZ).isoformat()


class Store:
    """Readable project JSON plus bounded, server-managed undo history."""
    STATE_KEYS = ('items', 'columns', 'values', 'preferences', 'next_ids')

    def __init__(self, path=None):
        supplied = Path(path or Path(__file__).parent / 'data' / 'tasks.json')
        self.path = supplied if supplied.suffix == '.json' else supplied.with_suffix('.json')
        self.legacy_path = supplied if supplied.suffix != '.json' else supplied.with_suffix('.sqlite3')
        self.history_path = self.path.with_name(self.path.stem + '.history.json')
        self.history_limit_bytes = int(os.environ.get('WORKY_HISTORY_MAX_BYTES', 10 * 1024 * 1024))
        if self.history_limit_bytes < 1024:
            raise ValueError('历史文件大小上限不能小于 1024 字节。')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state = None
        self._read_cache = None
        self._read_signature = None
        with self._locked():
            existing = self._load() if self.path.exists() else None
            if existing is not None and existing.get('format_version') == 2:
                colors = self._colors(existing)
                upgrade = any('labels' not in task or 'updates' not in task for task in existing['items'])
                trim_history = self.history_path.exists() and self.history_path.stat().st_size > self.history_limit_bytes
                if 'history' in existing or upgrade or trim_history or (colors and colors != existing['preferences'].get('label_colors')):
                    existing['history'] = self._load_history(existing)
                    snapshots = [existing]
                    for entries in existing['history'].values():
                        for entry in entries:
                            snapshots.extend([entry['before'], entry['after']])
                    for snapshot in snapshots:
                        for task in snapshot['items']:
                            task.setdefault('labels', [])
                            task.setdefault('updates', [])
                    if colors:
                        existing['preferences']['label_colors'] = colors
                    self._write(existing)
                return
            # Only migration reads the old database. It is never opened for writing.
            if self.legacy_path.exists():
                state = self._import_legacy(self.legacy_path)
            else:
                state = self._empty()
                if existing:
                    for key in ('items', 'columns', 'values', 'preferences'):
                        state[key] = existing.get(key, state[key])
            self._normalize(state)
            self._validate(state)
            self._write(state)

    @staticmethod
    def _empty():
        return dict(format_version=2, items=[], columns=[], values={}, preferences={},
                    next_ids=dict(item=1, column=1), history=dict(undo=[], redo=[]))

    @contextmanager
    def _locked(self):
        with self.path.with_name('.' + self.path.name + '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _load(self):
        signature = self._file_signature()
        try:
            state = json.loads(self.path.read_text(encoding='utf-8-sig'))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError(f'数据文件无法读取，请检查 {self.path}；原文件未被覆盖。') from exc
        if not isinstance(state, dict) or not isinstance(state.get('items'), list):
            raise ValueError('JSON 数据格式无效，原文件未被覆盖。')
        self._read_cache = state
        self._read_signature = signature
        return state

    def _file_signature(self):
        info = self.path.stat()
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _write(self, state):
        project = {key:value for key,value in state.items() if key != 'history'}
        history = state.get('history')
        if history is None:
            history = self._load_history(self._load()) if self.path.exists() else dict(undo=[], redo=[])
        payload = self._history_payload(project, history)
        previous_history = self.history_path.read_bytes() if self.history_path.exists() else None
        # History is prepared first. If the project replacement fails, restore it.
        # A crash between replacements is detected by the project digest on read.
        self._atomic_write(self.history_path, payload)
        try:
            self._atomic_write(self.path, (json.dumps(project, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8'))
        except Exception:
            if previous_history is None:
                self.history_path.unlink(missing_ok=True)
            else:
                self._atomic_write(self.history_path, previous_history)
            raise
        finally:
            self._read_cache = None
            self._read_signature = None

    @classmethod
    def _digest(cls, state):
        payload = {key:state[key] for key in cls.STATE_KEYS}
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()

    def _load_history(self, state):
        # Old exports are migrated once; imported project files never import undo.
        if 'history' in state:
            return state['history']
        if not self.history_path.exists():
            return dict(undo=[], redo=[])
        try:
            document = json.loads(self.history_path.read_bytes())
            if document.get('project_digest') != self._digest(state):
                return dict(undo=[], redo=[])
            history = {key:document[key] for key in ('undo', 'redo')}
            if any(not isinstance(entries, list) for entries in history.values()):
                raise ValueError('invalid stacks')
            return history
        except (ValueError, KeyError, AttributeError, UnicodeError) as exc:
            raise ValueError('服务端历史文件无法读取，项目 JSON 未被修改。') from exc

    def _history_payload(self, state, history):
        document = dict(format_version=1, project_digest=self._digest(state), **history)
        def encode():
            return (json.dumps(document, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
        payload = encode()
        while len(payload) > self.history_limit_bytes:
            if history['undo']:
                history['undo'].pop(0)
            elif history['redo']:
                history['redo'].pop()
                if history['redo']:
                    # Old redo steps are no longer individually available. Keep
                    # the first retained transition reversible from current data.
                    history['redo'][-1]['before'] = self._snapshot(state)
            else:
                break
            payload = encode()
        return payload

    @staticmethod
    def _atomic_write(path, payload):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent,
                                             prefix='.tasks-', suffix='.tmp', delete=False) as out:
                temporary = Path(out.name)
                out.write(payload)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    @staticmethod
    def _import_legacy(path):
        import sqlite3  # One-time, read-only migration; no database dependency in normal operation.
        con = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        con.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            state = Store._empty()
            state['items'] = [dict(r) for r in con.execute('SELECT * FROM tasks ORDER BY id')]
            if 'custom_columns' in tables:
                state['columns'] = [dict(r) for r in con.execute('SELECT * FROM custom_columns ORDER BY id')]
            if 'item_values' in tables:
                for row in con.execute('SELECT * FROM item_values'):
                    state['values'].setdefault(str(row['task_id']), {})[f"custom_{row['column_id']}"] = json.loads(row['value'])
            if 'preferences' in tables:
                state['preferences'] = {r['key']: json.loads(r['value']) for r in con.execute('SELECT * FROM preferences')}
            if 'sqlite_sequence' in tables:
                sequences = {r['name']:r['seq'] for r in con.execute('SELECT * FROM sqlite_sequence')}
                state['next_ids'] = dict(item=sequences.get('tasks',0)+1, column=sequences.get('custom_columns',0)+1)
            return state
        finally:
            con.close()

    @staticmethod
    def _normalize(state):
        for task in state['items']:
            task.setdefault('labels', [])
            task.setdefault('updates', [])
            task.setdefault('notes', '')
            task.setdefault('kind', 'task')
            task.setdefault('auto_closed', 0)
            task.setdefault('priority', '')
            task.setdefault('closed_date', None)
            task.setdefault('closed_at', None)
            task.setdefault('start_at', task['created_at'] if task['created_at'][:10] == task['start_date'] else datetime.combine(date.fromisoformat(task['start_date']), time.min, tzinfo=TZ).isoformat())
            if task['kind'] == 'note' and task['title'].strip():
                task['notes'] = task['title'] + ('\n\n' + task['notes'] if task['notes'].strip() and task['notes'] != task['title'] else '')
                task.update(title='', priority='', status='进行中', closed_date=None, closed_at=None, auto_closed=0)
            if task['status'] != '进行中':
                task['priority'] = ''
        for key, table in (('item','items'),('column','columns')):
            state['next_ids'][key] = max(state['next_ids'].get(key,1), max((t['id'] for t in state[table]), default=0)+1)
        Store._rollup(state)

    @staticmethod
    def _validate(state):
        ids = {t['id'] for t in state['items']}
        if len(ids) != len(state['items']):
            raise ValueError('条目编号重复。')
        lookup = {t['id']:t for t in state['items']}
        for task in state['items']:
            Store._validate_updates(task.get('updates', []))
            parent, seen = task['parent_id'], {task['id']}
            while parent is not None:
                if parent not in ids or parent in seen:
                    raise ValueError('条目父子关系无效。')
                seen.add(parent)
                parent = lookup[parent]['parent_id']

    @classmethod
    def _snapshot(cls, state):
        return {key:copy.deepcopy(state[key]) for key in cls.STATE_KEYS}

    def _read(self):
        # Private read-only view. Public getters copy only the requested fields,
        # rather than copying the entire undo history for each preference lookup.
        if self._state is not None:
            return self._state
        if self._read_cache is None or self._read_signature != self._file_signature():
            return self._load()
        return self._read_cache

    @contextmanager
    def action(self, label='修改', record=True):
        if self._state is not None:
            yield self._state
            return
        with self._locked():
            state = self._load()
            state['history'] = self._load_history(state)
            previous = copy.deepcopy(state)
            before = self._snapshot(state)
            self._state = state
            try:
                yield state
                self._validate(state)
                after = self._snapshot(state)
                if before != after and record:
                    state['history']['undo'].append(dict(label=label, at=datetime.now(TZ).isoformat(), before=before, after=after))
                    state['history']['redo'].clear()
                if state != previous:
                    self._write(state)
            finally:
                self._state = None
                # Discard even after an exception: the action may have modified
                # its in-memory state before validation failed.
                self._read_cache = None
                self._read_signature = None

    def export_json(self):
        # Normal files contain project data only; old embedded histories are
        # removed by initialization before this method is available.
        return self.path.read_bytes()

    @classmethod
    def parse_import(cls, content):
        try:
            data = json.loads(content.decode('utf-8-sig') if isinstance(content, bytes) else content,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError('不支持非有限数字。')))
            if not isinstance(data,dict) or not isinstance(data.get('items'),list):
                raise ValueError('需要包含 items 列表的项目 JSON 文件。')
            if data.get('format_version',1) not in (1,2):
                raise ValueError('不支持此数据文件版本。')
            state = cls._empty()
            for key in cls.STATE_KEYS:
                if key in data:
                    state[key] = copy.deepcopy(data[key])
            if not isinstance(state['columns'],list) or not isinstance(state['values'],dict) or not isinstance(state['preferences'],dict) or not isinstance(state['next_ids'],dict):
                raise ValueError('自定义列、参数或设置格式不正确。')
            for key in ('item','column'):
                if type(state['next_ids'].get(key)) is not int or state['next_ids'][key]<1:
                    raise ValueError('编号计数器格式不正确。')
            ids = set()
            for task in state['items']:
                if not isinstance(task,dict) or type(task.get('id')) is not int or task['id']<1 or task['id'] in ids:
                    raise ValueError('条目编号必须是互不重复的正整数。')
                ids.add(task['id'])
                for field in ('title','notes','start_date','created_at'):
                    if not isinstance(task.get(field),str):
                        raise ValueError('条目的名称、备注或日期字段不完整。')
                if not task['title'].strip() and not task['notes'].strip():
                    raise ValueError('条目的名称和备注不能同时为空。')
                cls._clean_labels(task.get('labels', []))
                cls._validate_updates(task.get('updates', []))
                if task.get('status') not in STATUSES or task.get('priority','') not in PRIORITIES or task.get('kind','task') not in ('task','note'):
                    raise ValueError('条目状态或优先级无效。')
                if type(task.get('position')) is not int or task['position']<0 or (task.get('parent_id') is not None and type(task['parent_id']) is not int):
                    raise ValueError('条目顺序或父条目编号无效。')
                task.setdefault('parent_id',None)
                date.fromisoformat(task['start_date'])
                datetime.fromisoformat(task['created_at'])
                if task.get('closed_date'):
                    date.fromisoformat(task['closed_date'])
                if task['status']!='进行中' and not task.get('closed_date'):
                    raise ValueError('已结题的任务必须有结束日期。')
                for field in ('start_at','closed_at'):
                    if task.get(field):
                        datetime.fromisoformat(task[field])
            columns = {}
            for col in state['columns']:
                if not isinstance(col,dict) or type(col.get('id')) is not int or col['id']<1 or col['id'] in columns:
                    raise ValueError('自定义列编号无效或重复。')
                if not isinstance(col.get('name'),str) or not col['name'].strip() or len(col['name'])>50 or col.get('field_type') not in ('text','number','date','checkbox'):
                    raise ValueError('自定义列名称或类型无效。')
                if any(c['name']==col['name'] for c in columns.values()):
                    raise ValueError('自定义列名称重复。')
                columns[col['id']] = col
            for tid,values in state['values'].items():
                if str(tid) not in {str(i) for i in ids} or not isinstance(values,dict):
                    raise ValueError('自定义值引用了不存在的条目。')
                for key,value in values.items():
                    if not key.startswith('custom_') or not key[7:].isdigit() or int(key[7:]) not in columns:
                        raise ValueError('自定义值引用了不存在的列。')
                    kind = columns[int(key[7:])]['field_type']
                    if value in ('',None):
                        continue
                    if kind=='number' and (type(value) not in (int,float) or not math.isfinite(value)):
                        raise ValueError('数字列包含无效值。')
                    if kind=='checkbox' and not isinstance(value,bool):
                        raise ValueError('复选框列包含无效值。')
                    if kind=='text' and not isinstance(value,str):
                        raise ValueError('文本列包含无效值。')
                    if kind=='date':
                        date.fromisoformat(value)
            prefs=state['preferences']
            if 'label_colors' in prefs and (not isinstance(prefs['label_colors'],dict) or any(value not in LABEL_COLORS for value in prefs['label_colors'].values())):
                raise ValueError('标签颜色设置无效。')
            for key in ('column_order','visible_columns','pinned_columns'):
                if key in prefs and (not isinstance(prefs[key],list) or any(not isinstance(k,str) for k in prefs[key])):
                    raise ValueError('列设置格式无效。')
            if 'column_widths' in prefs and (not isinstance(prefs['column_widths'],dict) or any(type(v) not in (int,float) or not 50<=v<=1600 for v in prefs['column_widths'].values())):
                raise ValueError('列宽设置无效。')
            if 'column_sortable' in prefs and (not isinstance(prefs['column_sortable'],dict) or any(not isinstance(v,bool) for v in prefs['column_sortable'].values())):
                raise ValueError('列排序设置无效。')
            sort=prefs.get('sort_state')
            if sort is not None and (not isinstance(sort,dict) or not isinstance(sort.get('key'),str) or sort.get('direction') not in ('asc','desc') or not isinstance(sort.get('tie_order',[]),list)):
                raise ValueError('排序设置无效。')
            cls._validate(state)
            cls._normalize(state)
            for task in state['items']:
                task['labels'] = cls._clean_labels(task.get('labels',[]))
            if 'label_options' in state['preferences']:
                state['preferences']['label_options'] = cls._clean_labels(state['preferences']['label_options'])
            colors = cls._colors(state)
            if colors:
                state['preferences']['label_colors'] = colors
            return cls._snapshot(state)
        except (TypeError,KeyError,AttributeError,UnicodeError) as exc:
            raise ValueError('JSON 字段格式不正确，请选择本应用导出的文件。') from exc

    def import_json(self, content):
        incoming = self.parse_import(content)
        with self.action('导入项目') as state:
            state.update(incoming)

    def history_status(self):
        with self._locked():
            history = self._load_history(self._read())
        return dict(can_undo=bool(history['undo']), can_redo=bool(history['redo']))

    def undo(self):
        return self._travel(False)

    def redo(self):
        return self._travel(True)

    def _travel(self, forward):
        with self.action('重做' if forward else '撤销', record=False) as state:
            source, destination = ('redo','undo') if forward else ('undo','redo')
            if not state['history'][source]:
                return False
            entry = state['history'][source].pop()
            state.update(copy.deepcopy(entry['after' if forward else 'before']))
            state['history'][destination].append(entry)
            return True

    @staticmethod
    def _task(state, task_id):
        task = next((t for t in state['items'] if t['id'] == task_id), None)
        if task is None:
            raise ValueError('条目已被删除或不存在。')
        return task

    @staticmethod
    def _siblings(state, parent_id, excluded=None):
        return sorted((t for t in state['items'] if t['parent_id'] == parent_id and t['id'] != excluded), key=lambda t:(t['position'],t['id']))

    @staticmethod
    def _rollup(state):
        items = {t['id']: t for t in state['items']}
        children, depths = {}, {}
        for task in items.values():
            if task['kind'] != 'task':
                continue
            parent, depth, nearest_task, seen = task['parent_id'], 0, None, {task['id']}
            while parent is not None and parent in items:
                if parent in seen:
                    raise ValueError('条目父子关系存在循环。')
                seen.add(parent)
                depth += 1
                if nearest_task is None and items[parent]['kind'] == 'task':
                    nearest_task = parent
                parent = items[parent]['parent_id']
            depths[task['id']] = depth
            if nearest_task is not None:
                children.setdefault(nearest_task, []).append(task['id'])
        for tid in sorted(depths, key=depths.get, reverse=True):
            task = items[tid]
            if task['status'] == '已经废弃':
                continue
            group = children.get(tid, [])
            settled = bool(group) and all(items[c]['status'] in ('已经完结', '已经废弃') for c in group)
            if settled:
                latest = max(items[c]['closed_date'] or today().isoformat() for c in group)
                moments = [items[c]['closed_at'] for c in group if items[c]['closed_date'] == latest]
                latest_moment = max(moments, key=datetime.fromisoformat) if moments and all(moments) else None
                task.update(status='已经完结', closed_date=latest, closed_at=latest_moment, auto_closed=1, priority='')
            elif task['auto_closed']:
                task.update(status='进行中', closed_date=None, closed_at=None, auto_closed=0)

    def all(self):
        return sorted(copy.deepcopy(self._read()['items']), key=lambda t:(t['position'],t['id']))

    def add(self, title, notes='', parent_id=None, start_date=None, kind=None, before_id=None, priority=''):
        title = title.strip()
        if kind not in (None, 'task', 'note'):
            raise ValueError('条目类型无效。')
        if kind == 'note' and title:
            notes = title + ('\n\n' + notes if notes.strip() and notes != title else '')
            title = ''
        if not title and not notes.strip():
            raise ValueError('请填写名称或备注。')
        if priority not in PRIORITIES:
            raise ValueError('优先级无效。')
        kind = 'task' if title else 'note'
        start = start_date or today()
        if start > today():
            raise ValueError('这是实际进度记录，开始日期不能晚于今天。')
        with self.action('添加条目') as state:
            if parent_id is not None:
                self._task(state, parent_id)
            siblings = self._siblings(state, parent_id)
            pos = max((t['position'] for t in siblings), default=-1)+1
            if before_id is not None:
                anchor = self._task(state, before_id)
                if anchor['parent_id'] != parent_id:
                    raise ValueError('插入位置已变化，请重新选择行间加号。')
                pos = anchor['position']
                for sibling in siblings:
                    if sibling['position'] >= pos:
                        sibling['position'] += 1
            created = datetime.now(TZ).isoformat()
            tid = state['next_ids']['item']
            state['next_ids']['item'] += 1
            state['items'].append(dict(id=tid, parent_id=parent_id, title=title, notes=notes, status='进行中',
                                       start_date=start.isoformat(), closed_date=None, position=pos, created_at=created,
                                       kind=kind, labels=[], updates=[], auto_closed=0, priority=priority if kind=='task' else '',
                                       start_at=created if start==today() else date_moment(start), closed_at=None))
            self._rollup(state)
            return tid

    def move(self, task_id, target_id, placement, view_order=None):
        if placement not in ('before', 'inside', 'after'):
            raise ValueError('移动位置无效。')
        with self.action('移动条目') as state:
            tasks = sorted(state['items'], key=lambda t:(t['position'],t['id']))
            if view_order is not None:
                if len(view_order)!=len(set(view_order)) or set(view_order)!={t['id'] for t in tasks}:
                    raise ValueError('条目已发生变化，请重新拖动。')
                rank = {tid:i for i,tid in enumerate(view_order)}
                tasks.sort(key=lambda t:rank[t['id']])
                counters = {}
                for task in tasks:
                    task['position'] = counters.get(task['parent_id'],0)
                    counters[task['parent_id']] = task['position']+1
            task = self._task(state, task_id)
            target = self._task(state, target_id) if target_id is not None else None
            if target_id == task_id or target_id in self.descendants(tasks, task_id):
                raise ValueError('不能移到自身或自己的子条目下面。')
            parent = None if target is None else target_id if placement=='inside' else target['parent_id']
            siblings = self._siblings(state,parent,task_id)
            index = len(siblings) if target is None else 0 if placement=='inside' else siblings.index(target)+(placement=='after')
            siblings.insert(index,task)
            task['parent_id'] = parent
            for i,sibling in enumerate(siblings):
                sibling['position'] = i
            self._rollup(state)

    @staticmethod
    def _validate_updates(updates):
        if not isinstance(updates,list):
            raise ValueError('更新必须是记录列表。')
        seen=set()
        for entry in updates:
            if not isinstance(entry,dict) or not isinstance(entry.get('id'),str) or not entry['id'] or entry['id'] in seen:
                raise ValueError('更新编号无效或重复。')
            seen.add(entry['id'])
            if not isinstance(entry.get('content'),str) or not entry['content'].strip():
                raise ValueError('更新内容不能为空。')
            for field in ('created_at','edited_at'):
                value=entry.get(field)
                if field=='edited_at' and value is None:
                    continue
                if not isinstance(value,str):
                    raise ValueError('更新时间格式无效。')
                try:
                    moment=datetime.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError('更新时间格式无效。') from exc
                if moment.tzinfo is None:
                    raise ValueError('更新时间必须包含时区。')

    @staticmethod
    def _update_timestamp(value):
        if value is None:
            return datetime.now(TZ).isoformat()
        if not isinstance(value,str):
            raise ValueError('更新时间格式无效。')
        try:
            moment=datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError('更新时间格式无效。') from exc
        if moment.tzinfo is None:
            moment=moment.replace(tzinfo=TZ)
        return moment.astimezone(TZ).isoformat()

    def add_update(self, task_id, content, created_at=None):
        if not isinstance(content,str) or not content.strip():
            raise ValueError('请填写更新内容。')
        moment=self._update_timestamp(created_at)
        with self.action('添加阶段更新') as state:
            task=self._task(state,task_id)
            entry=dict(id=str(uuid4()),content=content,created_at=moment,edited_at=None)
            task.setdefault('updates',[]).append(entry)
            return entry['id']

    def edit_update(self, task_id, update_id, content, expected_content=None, created_at=None, expected_created_at=None):
        if not isinstance(content,str) or not content.strip():
            raise ValueError('请填写更新内容。')
        moment=self._update_timestamp(created_at) if created_at is not None else None
        with self.action('编辑阶段更新') as state:
            entry=next((e for e in self._task(state,task_id).get('updates',[]) if e['id']==update_id),None)
            if entry is None or (expected_content is not None and entry['content']!=expected_content) or (expected_created_at is not None and entry['created_at']!=expected_created_at):
                raise ValueError('该更新已发生变化，请重新打开后编辑。')
            moment=moment or entry['created_at']
            if datetime.fromisoformat(moment)==datetime.fromisoformat(entry['created_at']):
                moment=entry['created_at']
            if entry['content']!=content or entry['created_at']!=moment:
                entry.update(content=content,created_at=moment,edited_at=datetime.now(TZ).isoformat())

    def delete_update(self, task_id, update_id, expected_content=None, expected_created_at=None):
        with self.action('删除阶段更新') as state:
            task=self._task(state,task_id)
            entry=next((e for e in task.get('updates',[]) if e['id']==update_id),None)
            if entry is None or (expected_content is not None and entry['content']!=expected_content) or (expected_created_at is not None and entry['created_at']!=expected_created_at):
                raise ValueError('该更新已发生变化，请重新确认。')
            task['updates'].remove(entry)

    @staticmethod
    def _clean_labels(labels):
        if not isinstance(labels,list) or any(not isinstance(label,str) or not label.strip() or len(label.strip())>50 for label in labels):
            raise ValueError('标签须为最多 50 个字的非空名称列表。')
        return list(dict.fromkeys(label.strip() for label in labels))

    def label_options(self):
        state = self._read()
        return list(dict.fromkeys([*state['preferences'].get('label_options',[]),
                                   *(label for task in state['items'] for label in task.get('labels',[]))]))

    @staticmethod
    def _colors(state):
        options = list(dict.fromkeys([*state['preferences'].get('label_options',[]),
                                      *(label for task in state['items'] for label in task.get('labels',[]))]))
        saved = state['preferences'].get('label_colors',{})
        colors = {name:saved[name] for name in options if name in saved}
        counts = {color:sum(value==color for value in colors.values()) for color in LABEL_COLORS}
        for name in options:
            if name not in colors:
                color = min(LABEL_COLORS, key=counts.get)
                colors[name] = color
                counts[color] += 1
        return colors

    def label_colors(self):
        return self._colors(self._read())

    def set_labels(self, task_id, labels):
        labels = self._clean_labels(labels)
        with self.action('修改标签') as state:
            task = self._task(state,task_id)
            if task['kind']!='task':
                raise ValueError('只能给任务分配标签。')
            task['labels'] = labels
            state['preferences']['label_options'] = list(dict.fromkeys([*self.label_options(),*labels]))
            state['preferences']['label_colors'] = self._colors(state)

    def reorder_labels(self, order):
        if not isinstance(order,list) or any(not isinstance(name,str) for name in order):
            raise ValueError('标签顺序无效。')
        with self.action('调整标签列表顺序') as state:
            options=self.label_options()
            if len(order)!=len(set(order)) or set(order)!=set(options):
                raise ValueError('标签列表已发生变化，请重新拖动。')
            state['preferences']['label_colors']=self._colors(state)
            state['preferences']['label_options']=list(order)

    def rename_label(self, name, new_name):
        new_name = self._clean_labels([new_name])[0]
        with self.action('重命名标签') as state:
            options = self.label_options()
            if name not in options:
                raise ValueError('该标签已被删除，请重新选择。')
            if name == new_name:
                return
            if new_name in options:
                raise ValueError('该标签名称已经存在。')
            colors = self._colors(state)
            colors[new_name] = colors.pop(name)
            state['preferences']['label_colors'] = colors
            state['preferences']['label_options'] = [new_name if label==name else label for label in options]
            for task in state['items']:
                task['labels'] = [new_name if label==name else label for label in task.get('labels',[])]

    def delete_label(self, name, expected_ids=None):
        with self.action('删除标签') as state:
            options = self.label_options()
            if name not in options:
                raise ValueError('该标签已被删除。')
            affected = {t['id'] for t in state['items'] if name in t.get('labels',[])}
            if expected_ids is not None and affected != set(expected_ids):
                raise ValueError('使用该标签的任务已发生变化，请重新确认。')
            colors = self._colors(state)
            colors.pop(name,None)
            state['preferences']['label_colors'] = colors
            state['preferences']['label_options'] = [label for label in options if label!=name]
            for task in state['items']:
                task['labels'] = [label for label in task.get('labels',[]) if label!=name]

    def set_priority(self, task_id, priority):
        if priority not in PRIORITIES:
            raise ValueError('优先级无效。')
        with self.action('修改优先级') as state:
            task = self._task(state,task_id)
            if task['kind'] != 'task' or task['status'] != '进行中':
                raise ValueError('只能给进行中的任务设置优先级。')
            task['priority'] = priority

    def set_status(self, task_id, status):
        if status not in STATUSES:
            raise ValueError('任务状态无效。')
        with self.action('修改状态') as state:
            task = self._task(state,task_id)
            if task['kind'] != 'task':
                raise ValueError('该条目不是任务。')
            end = None if status=='进行中' else task['closed_date'] if task['status']==status else today().isoformat()
            moment = None if status=='进行中' else task['closed_at'] if task['status']==status else datetime.now(TZ).isoformat()
            task.update(status=status,closed_date=end,closed_at=moment,auto_closed=0,priority=task['priority'] if status=='进行中' else '')
            self._rollup(state)

    def delete(self, task_id, expected_ids=None):
        with self.action('删除条目') as state:
            self._task(state,task_id)
            ids = {task_id} | self.descendants(state['items'],task_id)
            if expected_ids is not None and ids != set(expected_ids):
                raise ValueError('子条目已发生变化，请关闭窗口后重新确认删除范围。')
            state['items'] = [t for t in state['items'] if t['id'] not in ids]
            for tid in ids:
                state['values'].pop(str(tid),None)
            self._rollup(state)

    def columns(self):
        return sorted(copy.deepcopy(self._read()['columns']), key=lambda c:c['id'])

    @staticmethod
    def _column(state, column_id):
        col = next((c for c in state['columns'] if c['id']==column_id),None)
        if col is None:
            raise ValueError('自定义列不存在。')
        return col

    def add_column(self, name, field_type):
        name = name.strip()
        if not name or len(name)>50 or field_type not in ('text','number','date','checkbox'):
            raise ValueError('请填写有效列名和列类型。')
        with self.action('添加自定义列') as state:
            if any(c['name']==name for c in state['columns']):
                raise ValueError('该列名已经存在。')
            cid = state['next_ids']['column']
            state['next_ids']['column'] += 1
            state['columns'].append(dict(id=cid,name=name,field_type=field_type))
            return cid

    def rename_column(self, column_id, name):
        name = name.strip()
        if not name or len(name)>50:
            raise ValueError('列名须为 1–50 个字符。')
        with self.action('重命名自定义列') as state:
            col = self._column(state,column_id)
            if any(c['name']==name and c['id']!=column_id for c in state['columns']):
                raise ValueError('该列名已经存在。')
            col['name'] = name

    def delete_column(self, column_id, expected_name=None):
        with self.action('删除自定义列') as state:
            col = self._column(state,column_id)
            if expected_name is not None and col['name'] != expected_name:
                raise ValueError('该列已发生变化，请重新确认。')
            state['columns'].remove(col)
            key = f'custom_{column_id}'
            for values in state['values'].values():
                values.pop(key,None)
            prefs = state['preferences']
            for name in ('visible_columns','column_order','pinned_columns'):
                if name in prefs:
                    prefs[name] = [k for k in prefs[name] if k!=key]
            if 'column_widths' in prefs:
                prefs['column_widths'].pop(key,None)
            if 'column_sortable' in prefs:
                prefs['column_sortable'].pop(key,None)
            if prefs.get('sort_state') and prefs['sort_state']['key']==key:
                prefs['sort_state'] = None

    def values(self):
        return {int(tid):copy.deepcopy(values) for tid,values in self._read()['values'].items()}

    def set_value(self, task_id, column_id, value):
        with self.action('修改自定义内容') as state:
            col = self._column(state,column_id)
            self._task(state,task_id)
            if value not in ('',None):
                if col['field_type']=='number':
                    try:
                        value = float(value)
                        if not math.isfinite(value):
                            raise ValueError()
                    except (ValueError,TypeError):
                        raise ValueError('请输入有效数字。')
                elif col['field_type']=='date':
                    value = date.fromisoformat(value).isoformat()
                elif col['field_type']=='checkbox':
                    if not isinstance(value,bool):
                        raise ValueError('复选框值无效。')
                else:
                    value = str(value)
            state['values'].setdefault(str(task_id),{})[f'custom_{column_id}'] = value

    def preference(self, key, default=None):
        return copy.deepcopy(self._read()['preferences'].get(key, default))

    def set_preference(self, key, value):
        with self.action('修改表格设置') as state:
            state['preferences'][key] = copy.deepcopy(value)

    @staticmethod
    def descendants(tasks, task_id):
        found, pending = set(), [task_id]
        while pending:
            current = pending.pop()
            children = [t['id'] for t in tasks if t['parent_id']==current and t['id'] not in found]
            found.update(children)
            pending.extend(children)
        return found

    def update(self, task_id, title, notes, parent_id, status, start_date, closed_date=None):
        if status not in STATUSES or (not title.strip() and not notes.strip()):
            raise ValueError('请填写名称或备注。')
        kind = 'task' if title.strip() else 'note'
        if kind=='note':
            status,closed_date = '进行中',None
        with self.action('修改条目') as state:
            old = self._task(state,task_id)
            if parent_id==task_id or parent_id in self.descendants(state['items'],task_id):
                raise ValueError('不能把任务移到自身或自己的子任务下面。')
            if parent_id is not None:
                self._task(state,parent_id)
            if start_date>today():
                raise ValueError('开始日期不能晚于今天。')
            if status=='进行中':
                closed_date = None
            elif status!=old['status']:
                closed_date = today()
            else:
                closed_date = closed_date or date.fromisoformat(old['closed_date'])
            if closed_date and (closed_date>today() or (closed_date<start_date and not old['auto_closed'])):
                raise ValueError('结束日期须在开始日期和今天之间。')
            pos = old['position'] if parent_id==old['parent_id'] else max((t['position'] for t in self._siblings(state,parent_id)),default=-1)+1
            auto_closed = old['auto_closed'] if kind=='task' and status==old['status'] else 0
            start_at = old['start_at'] if start_date.isoformat()==old['start_date'] else date_moment(start_date)
            closed_at = None if closed_date is None else old['closed_at'] if closed_date.isoformat()==old['closed_date'] and status==old['status'] else date_moment(closed_date,end=True)
            old.update(title=title.strip(),notes=notes,parent_id=parent_id,status=status,start_date=start_date.isoformat(),
                       closed_date=closed_date.isoformat() if closed_date else None,position=pos,auto_closed=auto_closed,
                       start_at=start_at,closed_at=closed_at,priority=old['priority'] if status=='进行中' and kind=='task' else '',kind=kind)
            self._rollup(state)

    def reorder(self, parent_id, ids):
        with self.action('调整顺序') as state:
            siblings = self._siblings(state,parent_id)
            if len(ids)!=len(set(ids)) or set(ids)!={t['id'] for t in siblings}:
                raise ValueError('任务已发生变化，请刷新后重新排序。')
            lookup = {t['id']:t for t in siblings}
            for i,tid in enumerate(ids):
                lookup[tid]['position'] = i


def flatten(tasks, parent_id=None, depth=0):
    result = []
    for task in sorted((t for t in tasks if t['parent_id']==parent_id),key=lambda t:(t['position'],t['id'])):
        result.append((task,depth))
        result.extend(flatten(tasks,task['id'],depth+1))
    return result
