"""Presentation-only translations; persisted task values stay unchanged."""
import json
import re
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def english_catalog():
    return json.loads((Path(__file__).parent / 'assets/locales/en.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def _message_templates():
    templates=[]
    for source,target in english_catalog().items():
        parts=re.split(r'(\{\d+\})',source)
        if len(parts)==1:
            continue
        slots=[]
        pattern=''
        for part in parts:
            if re.fullmatch(r'\{\d+\}',part):
                slots.append(part)
                pattern+='(.*?)'
            else:
                pattern+=re.escape(part)
        templates.append((re.compile('^'+pattern+'$',re.DOTALL),slots,target))
    return templates


def translator(language):
    def tr(source,*values):
        source=str(source)
        result=source
        if language=='en':
            result=english_catalog().get(source)
            if result is None:
                result=source
                for pattern,slots,target in _message_templates():
                    match=pattern.fullmatch(source)
                    if match:
                        replacements=dict(zip(slots,match.groups()))
                        result=re.sub(r'\{\d+\}',lambda item:replacements[item.group()],target)
                        break
        return re.sub(r'\{(\d+)\}',lambda item:str(values[int(item.group(1))]) if int(item.group(1))<len(values) else item.group(),result)
    return tr
