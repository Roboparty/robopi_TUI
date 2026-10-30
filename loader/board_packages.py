"""TUI-owned versions for the five production packages, from the configured source."""
import datetime
from functools import cmp_to_key
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

PACKAGE_HOST = 'apt.roboparty.com'
FOCUS_PACKAGES = ('roboparty-base', 'roboparty-bms', 'roboparty-analyze',
                  'robopi-addon', 'robopi-tui')


def target_uri(uri):
    parsed = urlsplit(uri)
    return parsed.scheme in ('http', 'https') and (parsed.hostname or '').lower().rstrip('.') == PACKAGE_HOST


def deb822_sections(content):
    fields = {}
    key = None
    for line in content.splitlines() + ['']:
        if line.lstrip().startswith('#'):
            continue
        if not line.strip():
            if fields:
                yield fields
            fields, key = {}, None
        elif line[:1].isspace():
            if key is None:
                raise ValueError('Invalid Deb822 continuation')
            fields[key] += '\n' + line
        else:
            key, separator, value = line.partition(':')
            if not separator or not key.strip() or key.strip().lower() in fields:
                raise ValueError('Invalid or duplicate Deb822 field')
            key = key.strip().lower()
            fields[key] = value.strip()


def select_sources(source_list, source_parts):
    """Preserve architectures, suites, components and signing configuration."""
    paths = [Path(source_list)] + sorted(Path(source_parts).glob('*.list'))
    lines = []
    stanzas = []
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            match = re.match(r'^\s*deb\s+(?:\[[^\]]*\]\s+)?(\S+)', line)
            if match and target_uri(match[1]):
                lines.append(line.strip())
    for path in sorted(Path(source_parts).glob('*.sources')):
        for fields in deb822_sections(path.read_text()):
            if fields.get('enabled', 'yes').lower() == 'no' or 'deb' not in fields.get('types', '').split():
                continue
            uris = [uri for uri in fields.get('uris', '').split() if target_uri(uri)]
            if not uris:
                continue
            fields['uris'] = ' '.join(uris)
            fields['types'] = 'deb'
            stanzas.append('\n'.join(key + ': ' + value for key, value in fields.items()))
    if not lines and not stanzas:
        raise RuntimeError('No enabled apt.roboparty.com binary source is configured on this board')
    return '\n'.join(lines) + '\n', '\n\n'.join(stanzas) + '\n'


def refresh_target_indexes(execute, uid, source_list='/etc/apt/sources.list', source_parts='/etc/apt/sources.list.d'):
    if uid != 0:
        raise PermissionError('Refreshing APT indexes requires root; no automatic elevation was attempted')
    legacy, modern = select_sources(source_list, source_parts)
    with tempfile.TemporaryDirectory(prefix='roboparty-studio-apt-') as directory:
        root = Path(directory)
        # APT may drop privileges to _apt when reading the temporary sources.
        root.chmod(0o755)
        parts = root / 'sources.list.d'
        parts.mkdir(mode=0o755)
        selected = root / 'sources.list'
        selected.write_text(legacy)
        (parts / 'roboparty.sources').write_text(modern)
        result = execute(['apt-get', '-o', 'Dir::Etc::sourcelist=' + str(selected),
                          '-o', 'Dir::Etc::sourceparts=' + str(parts),
                          '-o', 'APT::Get::List-Cleanup=false',
                          '-o', 'APT::Update::Error-Mode=any',
                          '-o', 'Acquire::http::Timeout=10', '-o', 'Acquire::https::Timeout=10',
                          '-o', 'Acquire::Retries=0', 'update'], timeout=120)
        output = result['stdout'] + '\n' + result['stderr']
        if result['exitCode'] != 0 or re.search(r'(?im)^(?:Err:|E:)|Failed to fetch|Some index files failed', output):
            raise RuntimeError('apt.roboparty.com index refresh failed; latest versions are unconfirmed. ' + output[-5000:])
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def source_versions(cache, version_compare, native_arch, refreshed=False):
    """Do not use the global APT candidate: it may come from another source."""
    output = []
    for package in cache.packages:
        if package.name not in FOCUS_PACKAGES:
            continue
        versions = [v for v in package.version_list if v.arch in (native_arch, 'all') and
                    any((f.site or '').lower().rstrip('.') == PACKAGE_HOST for f, _ in v.file_list)]
        if not versions:
            installed = package.current_ver
            output.append(dict(name=package.name, installed=installed.ver_str if installed else None,
                               latest=None, architecture=native_arch, status='源中未找到 · 版本未确认',
                               source=PACKAGE_HOST))
            continue
        latest = max(versions, key=cmp_to_key(lambda left, right: version_compare(left.ver_str, right.ver_str)))
        installed = package.current_ver
        installed_version = installed.ver_str if installed else None
        if installed is None:
            state = '未安装'
        else:
            comparison = version_compare(installed_version, latest.ver_str)
            state = '可更新' if comparison < 0 else ('已是源最新版本' if comparison == 0 else '已安装版本高于源版本')
            # dpkg's current-state enum: 6 = fully installed.
            if package.current_state != 6:
                state = '安装状态异常 · ' + state
        if package.selected_state == 2:  # dpkg selection: hold
            state += ' · 已保留(hold)'
        if not refreshed:
            state = '缓存参考 · ' + state
        files = [f for f, _ in latest.file_list if (f.site or '').lower().rstrip('.') == PACKAGE_HOST]
        output.append(dict(name=package.name, architecture=latest.arch,
                           installed=installed_version, latest=latest.ver_str,
                           status=state, source='; '.join(sorted(set(
                               PACKAGE_HOST + ' · ' + f.archive + '/' + f.component for f in files)))))
    present = {row['name'] for row in output}
    output.extend(dict(name=name, installed=None, latest=None, architecture=native_arch,
                       status='源中未找到 · 版本未确认', source=PACKAGE_HOST)
                  for name in FOCUS_PACKAGES if name not in present)
    return sorted(output, key=lambda row: FOCUS_PACKAGES.index(row['name']))


def query_packages(execute, refresh=False):
    try:
        import apt_pkg
    except ImportError:
        raise RuntimeError('python3-apt is required to identify exact APT package origins; it was not installed automatically')
    apt_pkg.init()
    refreshed_at = refresh_target_indexes(execute, __import__('os').geteuid()) if refresh else None
    architecture = apt_pkg.config.find('APT::Architecture')
    cache = apt_pkg.Cache(None)
    items = source_versions(cache, apt_pkg.version_compare, architecture, refresh)
    if not items:
        raise RuntimeError('No packages for the board architecture were found in apt.roboparty.com indexes; latest versions are unconfirmed')
    return dict(items=items, sourceHost=PACKAGE_HOST, refreshedAt=refreshed_at,
                held=[p['name'] for p in items if '已保留(hold)' in p['status']],
                rebootRequired=Path('/var/run/reboot-required').exists())
