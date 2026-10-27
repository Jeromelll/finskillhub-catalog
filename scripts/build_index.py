"""Fetch the public FinSkillHub catalog and rebuild this repository's index."""
import json
import os
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
CATALOG_URL = 'https://finskillhub.com/data/catalog.json'
TIMEOUT_SECONDS = 30
CATEGORIES = {
    'research': 'Company research / 公司与行业研究',
    'earnings': 'Earnings / 财报分析',
    'valuation': 'Valuation & models / 估值与建模',
    'portfolio': 'Portfolio & risk / 组合与风险',
    'deals': 'Deals & diligence / 交易与尽调',
    'operations': 'Research operations / 研究流程',
    'data': 'Financial data / 金融数据',
    'macro': 'Macro & markets / 宏观与市场',
    'quant': 'Quant research / 量化与技术研究',
    'fixedincome': 'Fixed income / 固收与信用',
}


def is_public_github_url(value):
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    parts = parsed.path.strip('/').split('/')
    return (
        parsed.scheme == 'https'
        and parsed.netloc == 'github.com'
        and parsed.username is None
        and parsed.password is None
        and len(parts) >= 2
        and all(parts[:2])
        and not parsed.query
        and not parsed.fragment
    )


def validate_catalog(catalog):
    if not isinstance(catalog, dict):
        raise ValueError('catalog root must be an object')
    skills, sources = catalog.get('skills'), catalog.get('sources')
    if not isinstance(skills, list) or not skills:
        raise ValueError('catalog must contain a non-empty skills array')
    if not isinstance(sources, list) or not sources:
        raise ValueError('catalog must contain a non-empty sources array')
    for i, skill in enumerate(skills):
        if not isinstance(skill, dict):
            raise ValueError(f'skills[{i}] must be an object')
        for field in ('id', 'category', 'name', 'nameEn', 'author', 'license'):
            if not isinstance(skill.get(field), str) or not skill[field].strip():
                raise ValueError(f'skills[{i}].{field} must be a non-empty string')
        for field in ('repository', 'sourceUrl'):
            if not is_public_github_url(skill.get(field)):
                raise ValueError(f'skills[{i}].{field} must be an HTTPS github.com URL')
        if skill.get('licenseUrl') and not is_public_github_url(skill['licenseUrl']):
            raise ValueError(f'skills[{i}].licenseUrl must be an HTTPS github.com URL')
    for i, source in enumerate(sources):
        if not isinstance(source, dict) or not is_public_github_url(source.get('repository')):
            raise ValueError(f'sources[{i}].repository must be an HTTPS github.com URL')
    return catalog


def fetch_catalog(url=CATALOG_URL, opener=urlopen):
    request = Request(url, headers={'User-Agent': 'FinSkillHub-Catalog-Sync/1.0', 'Accept': 'application/json'})
    try:
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            if getattr(response, 'status', 200) != 200:
                raise ValueError(f'catalog server returned HTTP {response.status}')
            raw = response.read()
    except URLError as exc:
        raise ValueError(f'catalog fetch failed: {exc.reason}') from exc
    try:
        catalog = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('catalog response is not valid JSON') from exc
    return validate_catalog(catalog)


def clean(value):
    value = str(value).replace('\\', '\\\\').replace('|', '\\|').replace('\n', ' ')
    return value.replace('[', '\\[').replace(']', '\\]').replace('<', '&lt;').replace('>', '&gt;')


def link(label, url):
    return f'[{clean(label)}](<{url}>)'


def render_index(catalog):
    lines = [
        '# FinSkillHub Skill Catalog', '',
        'Generated from the public website catalog at `https://finskillhub.com/data/catalog.json`.', '',
        f"{len(catalog['skills'])} skills · {len(catalog['sources'])} source repositories · {clean(catalog.get('updatedAt', 'date unavailable'))}", '',
        'Catalog inclusion records source and license review. Execution in a host environment is not implied.', '',
    ]
    categories = list(CATEGORIES.items())
    known = {key for key, _ in categories}
    categories.extend((key, clean(key)) for key in sorted({s['category'] for s in catalog['skills']} - known))
    for category, title in categories:
        skills = [s for s in catalog['skills'] if s['category'] == category]
        if not skills:
            continue
        lines.extend([f'## {title}', '', '| Skill | Author | License |', '| --- | --- | --- |'])
        for skill in skills:
            lines.append(
                f"| {link(skill['name'], skill['sourceUrl'])}<br>{clean(skill['nameEn'])} | "
                f"{link(skill['author'], skill['repository'])} | {clean(skill['license'])} |"
            )
        lines.append('')
    return '\n'.join(lines) + '\n'


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def sync_and_build(root=ROOT, url=CATALOG_URL, opener=urlopen):
    catalog = fetch_catalog(url, opener)
    json_text = json.dumps(catalog, ensure_ascii=False, indent=2) + '\n'
    index_text = render_index(catalog)
    # Validate and render fully before replacing either tracked file.
    atomic_write(root / 'data/catalog.json', json_text)
    atomic_write(root / 'docs/CATALOG.md', index_text)
    print(json.dumps({'indexedSkills': len(catalog['skills']), 'sources': len(catalog['sources'])}))


if __name__ == '__main__':
    sync_and_build()
