import json
import base64
import os
from uuid import uuid4
from datetime import date, datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from markdown_it import MarkdownIt

from model import Store, STATUSES, today, flatten, TZ

logo_svg = (Path(__file__).parent / 'assets' / 'logo.svg').read_text(encoding='utf-8-sig')
logo_url = 'data:image/svg+xml;base64,' + base64.b64encode(logo_svg.encode('utf-8')).decode('ascii')
st.set_page_config(page_title='Worky · 让工作井然有序', page_icon=logo_url, layout='wide', initial_sidebar_state='collapsed')
st.markdown('''<style>
.block-container {padding:.6rem 1rem 0; max-width:none;}
.stApp {background:#f7f9fc;}
h1,h2,h3 {font-family:'Segoe UI','Microsoft YaHei',sans-serif;}
[data-testid="stMetric"] {padding:0;}
[data-testid="stMetricValue"] {font-size:1.6rem;}
[data-testid="stMetricLabel"] {font-size:.8rem;}
[data-testid="stSidebar"] {display:none;}
[data-testid="stHeader"] {height:1rem; background:transparent;}
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stAppDeployButton"], .stDeployButton, #MainMenu {display:none !important;}
div[data-testid="stVerticalBlock"] {gap:.65rem;}
button[data-testid="stPopoverButton"]>div>div[aria-hidden="true"] {display:none;}
div[data-testid="stColumn"]:has(.st-key-status_filter) {flex:0 0 auto;min-width:0;width:max-content;}
div[data-testid="stColumn"]:has(.app-brand) {flex:0 0 112px;min-width:112px;}
.st-key-status_filter button {height:38px;padding:0 12px;border:0!important;border-radius:6px!important;background:transparent!important;color:#64748b!important;box-shadow:none!important;}
.st-key-status_filter button p {font-size:16px!important;font-weight:500;white-space:nowrap;}
.st-key-status_filter button[aria-checked="true"] {background:#fff8e8!important;color:#6c5428!important;box-shadow:inset 0 0 0 1px #e9dfca!important;}
.st-key-status_filter button[aria-checked="true"] p {font-weight:750;}
.st-key-status_filter button:hover {background:#f0f3f8!important;}
.st-key-status_filter button[aria-checked="true"]:hover {background:#fff4dd!important;}
div[data-testid="stColumn"]:has(.st-key-time_unit) {flex:0 0 94px;min-width:94px;}
div[data-testid="stColumn"]:has(.st-key-undo_action),
div[data-testid="stColumn"]:has(.st-key-redo_action),
div[data-testid="stColumn"]:has(.st-key-import_action),
div[data-testid="stColumn"]:has(.st-key-export_action,.st-key-theme_action) {flex:0 0 32px;min-width:32px;}
:is(.st-key-undo_action,.st-key-redo_action,.st-key-import_action,.st-key-export_action,.st-key-theme_action) button {
    width:32px;min-width:32px;height:32px;min-height:32px;padding:0;border-radius:6px;margin-top:3px;
    display:flex;align-items:center;justify-content:center;
}
:is(.st-key-undo_action,.st-key-redo_action,.st-key-import_action,.st-key-export_action,.st-key-theme_action) [data-testid="stIconMaterial"] {
    font-size:18px;line-height:18px;
}
</style>''', unsafe_allow_html=True)
db = Store(os.environ.get('TASK_DATA_PATH') or os.environ.get('TASK_DB_PATH'))
dark_mode = db.preference('color_theme', 'light') == 'dark'
dark_css = (Path(__file__).parent / 'assets' / 'dark-app.css').read_text() if dark_mode else ''
st.markdown(f'<style>{dark_css}</style>', unsafe_allow_html=True)
tasks = db.all()
ordered = flatten(tasks)
by_id = {t['id']: t for t in tasks}
labels = {None: '无 · 顶层条目'}
labels.update({t['id']: '　' * depth + t['title'] + f" · #{t['id']}" for t, depth in ordered})
custom_columns = db.columns()
markdown = MarkdownIt('commonmark', {'html': False, 'breaks': True}).enable(['strikethrough', 'table'])
builtins = [
    dict(key='id', name='编号', type='id', width=54),
    dict(key='title', name='条目', type='title', width=235),
    dict(key='priority', name='P', ariaName='优先级', type='priority', width=50),
    dict(key='labels', name='标签', type='labels', width=140),
    dict(key='notes', name='备注', type='text', width=160),
    dict(key='updates', name='更新', type='updates', width=150),
    dict(key='start_date', name='开始日期', type='date', width=116),
    dict(key='closed_date', name='结束日期', type='date', width=116),
    dict(key='created_at', name='创建时间', type='readonly', width=165),
]
columns = builtins + [dict(key=f"custom_{c['id']}", name=c['name']+('（自定义）' if c['name'] in {b['name'] for b in builtins} else ''), rawName=c['name'], type=c['field_type'], width=150) for c in custom_columns]
default_columns = ['id', 'title', 'priority', 'labels', 'notes', 'updates', 'start_date', 'closed_date']
saved_order = db.preference('column_order', [])
if not db.preference('priority_column_added', False):
    with db.action('初始化视图', record=False):
        if not db.preference('priority_column_added', False):
            saved_order = [key for key in (saved_order or [c['key'] for c in columns]) if key != 'priority']
            saved_order.insert(saved_order.index('title')+1 if 'title' in saved_order else 0, 'priority')
            db.set_preference('column_order', saved_order)
            db.set_preference('visible_columns', list(dict.fromkeys(db.preference('visible_columns', default_columns) + ['priority'])))
            db.set_preference('priority_column_added', True)
if not db.preference('label_column_added', False):
    with db.action('初始化 label 列', record=False):
        if not db.preference('label_column_added', False):
            saved_order = [key for key in (saved_order or [c['key'] for c in columns]) if key != 'labels']
            saved_order.insert(saved_order.index('priority')+1 if 'priority' in saved_order else len(saved_order), 'labels')
            db.set_preference('column_order', saved_order)
            db.set_preference('visible_columns', list(dict.fromkeys(db.preference('visible_columns', default_columns) + ['labels'])))
            db.set_preference('label_column_added', True)
if not db.preference('updates_column_added', False):
    with db.action('初始化更新列', record=False):
        if not db.preference('updates_column_added', False):
            saved_order=[key for key in (saved_order or [c['key'] for c in columns]) if key!='updates']
            saved_order.insert(saved_order.index('notes')+1 if 'notes' in saved_order else len(saved_order),'updates')
            db.set_preference('column_order',saved_order)
            db.set_preference('visible_columns',list(dict.fromkeys(db.preference('visible_columns',default_columns)+['updates'])))
            db.set_preference('updates_column_added',True)
rank = {key: i for i, key in enumerate(saved_order)}
columns.sort(key=lambda col: rank.get(col['key'], len(rank)))
column_widths = db.preference('column_widths', {})
for col in columns:
    width = column_widths.get(col['key'])
    if isinstance(width, (int, float)) and 50 <= width <= 1600:
        col['width'] = int(width)
sortability = {col['key']: db.preference('column_sortable', {}).get(col['key'], col['key'] not in ('title', 'notes', 'labels', 'updates') and col['type'] != 'text') for col in columns}
active_sort = db.preference('sort_state', None)
needs_sort_init = (not db.preference('title_sort_default_off_applied', False)
                   or db.preference('column_sortable', {}) != sortability
                   or bool(active_sort and not sortability.get(active_sort.get('key'), False)))
if needs_sort_init:
    with db.action('初始化排序设置', record=False):
        if not db.preference('title_sort_default_off_applied', False):
            sortability['title'] = False
            db.set_preference('title_sort_default_off_applied', True)
        db.set_preference('column_sortable', sortability)
        active_sort = db.preference('sort_state', None)
        if active_sort and not sortability.get(active_sort.get('key'), False):
            db.set_preference('sort_state', None)
for col in columns:
    col['sortable'] = sortability[col['key']]
visible = db.preference('visible_columns', default_columns)
visible = [c['key'] for c in columns if c['key'] in visible or c['key'] == 'title']
sunday_date = components.declare_component('sunday_date', path=str(Path(__file__).parent / 'datepicker'))


def pick_date(label, value, key):
    st.caption(label)
    chosen = sunday_date(darkMode=dark_mode, value=value.isoformat(), max=today().isoformat(), label=label, key=key, default=value.isoformat())
    return date.fromisoformat(chosen)


def queue_date_conflict(task_id, title, notes, parent_id, status, start, end):
    st.session_state['date_conflict'] = dict(task_id=task_id, title=title, notes=notes, parent_id=parent_id,
                                             status=status, start=start.isoformat(), end=end.isoformat())
    st.rerun()


@st.dialog('日期冲突', width='medium', dismissible=False)
def resolve_date_conflict(change):
    st.warning(f"结束日期 {change['end']} 早于开始日期 {change['start']}。")
    st.write(f"是否将开始日期也改为 {change['end']}？")
    cancel, adjust = st.columns([1, 2])
    if cancel.button('取消', width='stretch'):
        st.session_state.pop('date_conflict', None)
        st.rerun()
    if adjust.button('同时修改开始日期', type='primary', width='stretch'):
        try:
            end = date.fromisoformat(change['end'])
            db.update(change['task_id'], change['title'], change['notes'], change['parent_id'], change['status'], end, end)
            st.session_state.pop('date_conflict', None)
            st.rerun()
        except (ValueError, StopIteration) as exc:
            st.error(str(exc) or '条目已发生变化，请取消后重试。')


@st.dialog('条目详情', width='large')
def edit_item(task_id):
    task = by_id[task_id]
    is_task = task['kind'] == 'task'
    st.caption(f"#{task_id} · 创建于 {task['created_at'][:16].replace('T', ' ')}")
    with st.form(f'edit_item_{task_id}'):
        title = st.text_area('名称（可留空，仅填写备注时作为文本条目）', task['title'], height=90)
        notes = st.text_area('备注 / 补充说明', task['notes'], height=170)
        status = task['status']
        start = date.fromisoformat(task['start_date'])
        closed = date.fromisoformat(task['closed_date']) if task['closed_date'] else None
        if is_task:
            a, b = st.columns(2)
            with a:
                start = pick_date('开始日期', start, f'detail_start_{task_id}')
            if closed:
                with b:
                    if task['auto_closed']:
                        st.caption('结束日期（随子任务计算）')
                        st.write(closed.isoformat())
                    else:
                        closed = pick_date('结束日期', closed, f'detail_end_{task_id}')
            st.caption(f"{status} · 在表格前方勾选完成，右键标记废弃。父子任务独立记录。")
        if st.form_submit_button('保存修改', type='primary', width='stretch'):
            try:
                if title.strip() and closed and closed < start and not task['auto_closed']:
                    queue_date_conflict(task_id, title, notes, task['parent_id'], status, start, closed)
                db.update(task_id, title, notes, task['parent_id'], status, start, closed)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with st.expander('Markdown 预览'):
        if task['title']:
            st.markdown(task['title'])
        st.markdown(task['notes'] or '')


@st.dialog('确认删除', width='medium')
def confirm_delete(task_id):
    affected = {task_id} | db.descendants(tasks, task_id)
    entries = [(t, depth) for t, depth in ordered if t['id'] in affected]
    st.warning(f'将删除 {len(affected)} 个条目，包括此条目及其全部子条目。删除后可撤销。')
    st.text('\n'.join('  ' * (depth - entries[0][1]) + f"#{t['id']} {t['title'] or t['notes'][:100]}" for t, depth in entries))
    cancel, confirm = st.columns(2)
    if cancel.button('取消', width='stretch'):
        st.rerun()
    if confirm.button('确认删除', type='primary', width='stretch'):
        try:
            db.delete(task_id, expected_ids=affected)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def patch_item(event):
    tid, field, value = int(event['id']), event['field'], event['value']
    if field.startswith('custom_'):
        db.set_value(tid, int(field.split('_')[1]), value)
        return
    if field == 'labels':
        db.set_labels(tid, value)
        st.session_state['labels_nonce'] = event['nonce']
        return
    if field == 'priority':
        db.set_priority(tid, value)
        return
    if field not in ('title', 'notes', 'status', 'start_date', 'closed_date'):
        raise ValueError('该列不可编辑。')
    task = by_id[tid]
    if task['kind'] == 'note' and field in ('status', 'start_date', 'closed_date'):
        raise ValueError('纯文本条目没有任务状态或日期。')
    changed = dict(task)
    changed[field] = value
    start = date.fromisoformat(changed['start_date'])
    closed = date.fromisoformat(changed['closed_date']) if changed['closed_date'] else None
    if field == 'closed_date' and closed and closed < start:
        queue_date_conflict(tid, changed['title'], changed['notes'], changed['parent_id'], changed['status'], start, closed)
    db.update(tid, changed['title'], changed['notes'], changed['parent_id'], changed['status'], start, closed)


@st.dialog('确认删除自定义列', width='medium')
def confirm_delete_column(column_id):
    col = next(c for c in custom_columns if c['id']==column_id)
    count = sum(f'custom_{column_id}' in row for row in db.values().values())
    st.warning(f"将删除自定义列「{col['name']}」及其中 {count} 个条目的值。删除后可撤销。")
    cancel, confirm = st.columns(2)
    if cancel.button('取消', width='stretch'):
        st.rerun()
    if confirm.button('确认删除该列', type='primary', width='stretch'):
        try:
            db.delete_column(column_id, expected_name=col['name'])
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


@st.dialog('确认删除标签', width='medium')
def confirm_delete_label(name, expected_ids):
    st.warning(f'将删除标签「{name}」，并从 {len(expected_ids)} 个任务中移除该标签。任务内容保留，删除后可撤销。')
    cancel, confirm = st.columns(2)
    if cancel.button('取消', width='stretch'):
        st.rerun()
    if confirm.button('确认删除标签', type='primary', width='stretch'):
        try:
            db.delete_label(name, expected_ids=expected_ids)
            st.session_state['history_nonce'] = str(uuid4())
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


@st.dialog('导入 JSON', width='medium')
def import_data(content, filename):
    st.caption(filename)
    try:
        incoming = db.parse_import(content)
    except ValueError as exc:
        st.error(str(exc))
        return
    st.write(f"文件包含 {len(incoming['items'])} 个条目、{len(incoming['columns'])} 个自定义列。")
    st.warning(f"导入将替换当前 {len(db.all())} 个条目及列设置。确认后立即保存，可通过撤销恢复。")
    cancel, confirm = st.columns(2)
    if cancel.button('取消导入', width='stretch'):
        st.rerun()
    if confirm.button('确认导入', type='primary', width='stretch'):
        try:
            db.import_json(content)
            st.session_state['history_nonce'] = str(uuid4())
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def toggle_theme():
    db.set_preference('color_theme', 'light' if db.preference('color_theme', 'light') == 'dark' else 'dark')


def perform_history(operation, nonce=None):
    getattr(db, operation)()
    st.session_state['history_nonce'] = nonce or str(uuid4())
    st.session_state.pop('date_conflict', None)


history = db.history_status()
brand_col, summary, spacer, unit_col, undo_col, redo_col, import_col, export, theme_col = st.columns([1, 2.8, 2.2, 1.1, .45, .45, .7, .7, .45])
theme_col.button('', icon=':material/light_mode:' if dark_mode else ':material/dark_mode:',
                 help='切换到浅色模式' if dark_mode else '切换到暗黑模式', key='theme_action', on_click=toggle_theme, width='stretch')
undo_col.button('', icon=':material/undo:', help='撤销（Ctrl+Z）', key='undo_action',
                disabled=not history['can_undo'], on_click=perform_history, args=('undo',), width='stretch')
redo_col.button('', icon=':material/redo:', help='重做（Ctrl+Y）', key='redo_action',
                disabled=not history['can_redo'], on_click=perform_history, args=('redo',), width='stretch')
counts = [sum(t['kind']=='task' and t['status']==status for t in tasks) for status in STATUSES]
brand_col.markdown(f'<div class="app-brand" style="height:38px;display:flex;align-items:center;gap:9px;white-space:nowrap;font-size:20px;font-weight:650;color:#334155"><img src="{logo_url}" width="30" height="30" alt="Worky标志" style="display:block"><span>Worky</span></div>', unsafe_allow_html=True)
filter_labels = {'全部状态': f'全部 {len(tasks)}', '进行中': f'进行中 {counts[0]}', '已经完结': f'完结 {counts[1]}', '已经废弃': f'废弃 {counts[2]}'}
if st.session_state.get('status_filter') not in (None, *filter_labels):
    st.session_state['status_filter'] = '全部状态'
state_filter = summary.segmented_control('筛选状态', list(filter_labels), format_func=filter_labels.get,
                                          default='全部状态', required=True, label_visibility='collapsed', key='status_filter', width='content', wrap=False)
time_unit = unit_col.selectbox('时间轴单位', ['按天', '按周', '按月', '按年'], label_visibility='collapsed', key='time_unit')
values = db.values()
effective_priorities = {}
for task in tasks:
    current, seen = task, set()
    effective = ''
    while current and current['id'] not in seen:
        seen.add(current['id'])
        if current['kind'] == 'task' and current['priority']:
            effective = current['priority']
            break
        current = by_id.get(current['parent_id'])
    effective_priorities[task['id']] = effective
rendered_updates = {task['id']:{entry['id']:markdown.render(entry['content']) for entry in task.get('updates',[])} for task in tasks}
rendered = {}
for task in tasks:
    rendered[task['id']] = {'title': markdown.renderInline(task['title']) if task['kind'] == 'task' else markdown.render(task['notes']),
                            'notes': markdown.render(task['notes']) if task['kind']=='task' else ''}
    for col in custom_columns:
        if col['field_type'] == 'text':
            key = f"custom_{col['id']}"
            rendered[task['id']][key] = markdown.render(str(values.get(task['id'], {}).get(key) or ''))
export.download_button('', db.export_json(), 'tasks.json', 'application/json', icon=':material/download:', help='导出 JSON', key='export_action', width='stretch')
if st.session_state.get('notice'):
    st.error(st.session_state.pop('notice'))

grid = components.declare_component('task_tree_grid', path=str(Path(__file__).parent / 'grid'))
event = grid(darkMode=dark_mode, items=tasks, columns=[c for c in columns if c['key'] in visible], allColumns=columns, values=values, rendered=rendered, renderedUpdates=rendered_updates, effectivePriorities=effective_priorities,
             sortState=db.preference('sort_state', None), now=datetime.now(TZ).isoformat(), timezone=TZ.key,
             pinnedColumns=db.preference('pinned_columns', []),
             createdNonce=st.session_state.get('created_nonce'),
             errorNonce=st.session_state.get('error_nonce'), updateNonce=st.session_state.get('update_nonce'),
             moveNonce=st.session_state.get('move_nonce'), widthsNonce=st.session_state.get('widths_nonce'),
             historyNonce=st.session_state.get('history_nonce'), historyStatus=history,
             labelOptions=db.label_options(), labelColors=db.label_colors(), labelsNonce=st.session_state.get('labels_nonce'), renameNonce=st.session_state.get('rename_nonce'), labelOrderNonce=st.session_state.get('label_order_nonce'),
             today=today().isoformat(), stateFilter=state_filter, timeUnit=time_unit,
             key='main_grid', default=None)
json_import_button = components.declare_component('json_import_button', path=str(Path(__file__).parent / 'import_button'))
with import_col:
    selected_file = json_import_button(darkMode=dark_mode, key='import_action', default=None)
if selected_file and selected_file.get('nonce') != st.session_state.get('last_import_nonce'):
    st.session_state['last_import_nonce'] = selected_file.get('nonce')
    if selected_file.get('error'):
        st.error(selected_file['error'])
    elif isinstance(selected_file.get('content'), str):
        import_data(selected_file['content'].encode('utf-8'), selected_file.get('filename', 'tasks.json'))

if event and event.get('nonce') != st.session_state.get('last_event'):
    st.session_state['last_event'] = event.get('nonce')
    try:
        op = event['op']
        if op in ('undo', 'redo'):
            perform_history(op, event['nonce'])
            st.rerun()
        elif op in ('update_add','update_edit','update_delete'):
            task_id=int(event['id'])
            if op=='update_add':
                db.add_update(task_id,event['content'],created_at=event.get('created_at'))
            elif op=='update_edit':
                db.edit_update(task_id,event['update_id'],event['content'],created_at=event.get('created_at'),
                               expected_content=event.get('expected_content'),expected_created_at=event.get('expected_created_at'))
            else:
                db.delete_update(task_id,event['update_id'],expected_content=event.get('expected_content'),
                                 expected_created_at=event.get('expected_created_at'))
            st.session_state['update_nonce']=event['nonce']
            st.rerun()
        elif op == 'patch':
            patch_item(event)
            st.rerun()
        elif op == 'reorder_labels':
            db.reorder_labels(event['order'])
            st.session_state['label_order_nonce'] = event['nonce']
            st.rerun()
        elif op == 'rename_label':
            db.rename_label(event['name'], event['new_name'])
            st.session_state['rename_nonce'] = event['nonce']
            st.rerun()
        elif op == 'delete_label':
            affected = {t['id'] for t in db.all() if event['name'] in t.get('labels', [])}
            confirm_delete_label(event['name'], affected)
        elif op == 'move':
            with db.action('移动条目'):
                db.move(int(event['id']), int(event['target']) if event.get('target') is not None else None, event['placement'], view_order=event.get('view_order'))
                db.set_preference('sort_state', None)
            st.session_state['move_nonce'] = event['nonce']
            st.rerun()
        elif op == 'edit':
            edit_item(int(event['id']))
        elif op == 'complete':
            if not isinstance(event.get('checked'), bool):
                raise ValueError('完成标记无效。')
            db.set_status(int(event['id']), '已经完结' if event['checked'] else '进行中')
            st.rerun()
        elif op == 'abandon':
            db.set_status(int(event['id']), '已经废弃')
            st.rerun()
        elif op == 'reopen':
            db.set_status(int(event['id']), '进行中')
            st.rerun()
        elif op == 'delete':
            confirm_delete(int(event['id']))
        elif op == 'create':
            db.add(event['title'], event.get('notes', ''), event.get('parent_id'),
                   date.fromisoformat(event.get('start_date') or today().isoformat()), before_id=event.get('before_id'), priority=event.get('priority', ''))
            st.session_state['created_nonce'] = event['nonce']
            st.rerun()
        elif op == 'columns':
            keys = {c['key'] for c in columns}
            db.set_preference('visible_columns', [key for key in event['visible'] if key in keys] + ['title'])
            st.rerun()
        elif op == 'column_order':
            order = event['order']
            if len(order) != len(set(order)) or set(order) != set(visible):
                raise ValueError('显示列已发生变化，请重新拖动。')
            reordered = iter(order)
            db.set_preference('column_order', [next(reordered) if c['key'] in visible else c['key'] for c in columns])
            st.rerun()
        elif op == 'column_widths':
            widths = event['widths']
            valid_keys = {c['key'] for c in columns}
            if not isinstance(widths, dict) or any(key not in valid_keys or not isinstance(value, (int, float)) or not 50 <= value <= 1600 for key, value in widths.items()):
                raise ValueError('列宽无效。')
            db.set_preference('column_widths', {**column_widths, **{key: int(value) for key, value in widths.items()}})
            st.session_state['widths_nonce'] = event['nonce']
            st.rerun()
        elif op == 'column_sortable':
            key, allowed = event['key'], event['sortable']
            if key not in sortability or not isinstance(allowed, bool):
                raise ValueError('排序设置无效。')
            with db.action('设置列是否排序'):
                db.set_preference('column_sortable', {**sortability, key: allowed})
                if not allowed and (db.preference('sort_state', None) or {}).get('key') == key:
                    db.set_preference('sort_state', None)
            st.rerun()
        elif op == 'sort':
            if not sortability.get(event['key'], False) or event['direction'] not in ('asc','desc','none'):
                raise ValueError('排序选项无效。')
            db.set_preference('sort_state', None if event['direction']=='none' else dict(key=event['key'], direction=event['direction'], tie_order=event.get('tie_order', [])))
            st.rerun()
        elif op == 'pin_column':
            if event['key'] not in {c['key'] for c in columns}:
                raise ValueError('列不存在。')
            pinned = db.preference('pinned_columns', [])
            if event['key'] in pinned:
                pinned.remove(event['key'])
            else:
                pinned.append(event['key'])
            db.set_preference('pinned_columns', pinned)
            st.rerun()
        elif op == 'add_column':
            if event['name'].strip() in {c['name'] for c in builtins}:
                raise ValueError('该名称与内置列重复。')
            with db.action('添加自定义列'):
                cid = db.add_column(event['name'], event['field_type'])
                allowed = event.get('sortable', event['field_type'] != 'text')
                if not isinstance(allowed, bool):
                    raise ValueError('排序设置无效。')
                db.set_preference('column_sortable', {**sortability, f'custom_{cid}': allowed})
                db.set_preference('visible_columns', visible + [f'custom_{cid}'])
            st.rerun()
        elif op == 'rename_column':
            if event['name'].strip() in {c['name'] for c in builtins}:
                raise ValueError('该名称与内置列重复。')
            db.rename_column(int(event['id']), event['name'])
            st.session_state['rename_nonce'] = event['nonce']
            st.rerun()
        elif op == 'delete_column':
            confirm_delete_column(int(event['id']))
    except (ValueError, KeyError, StopIteration) as exc:
        st.session_state['error_nonce'] = event['nonce']
        st.session_state['notice'] = str(exc)
        st.rerun()

if st.session_state.get('date_conflict'):
    resolve_date_conflict(st.session_state['date_conflict'])
