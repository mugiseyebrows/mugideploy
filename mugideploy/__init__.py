import textwrap
import pefile
import os
import json
import re
import shutil
import os
import argparse
import glob
import subprocess
from colorama import Fore, Back, Style, init as colorama_init
from importlib.machinery import SourceFileLoader
from dataclasses import dataclass, field
from collections import defaultdict
import zipfile
from urllib.parse import quote as urlquote
from urllib.request import urlretrieve
import hashlib
import sys
import itertools
import functools
import contextlib
import datetime
from typing import Any

# set DEBUG_MUGIDEPLOY=1

def load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def save_json(path: str, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

def update_changelog(path, version, message):
    if path is None:
        path = os.path.join(os.getcwd(), 'changelog.json')
    data: list[dict] = []
    if os.path.exists(path):
        data = load_json(path)
    data.append({
        'version': version,
        'text': message
    })
    save_json(path, data)

@contextlib.contextmanager
def open_(path, *args, **kwargs):
    if path is None:
        f = sys.stdout
    else:
        f = open(path, *args, **kwargs)
    try:
        yield f
    finally:
        if path is None:
            pass
        else:
            f.close()

@dataclass
class Config:
    name: str = None
    version: str = None
    bin: list[Any] = field(default_factory=list)
    plugins: list[Any] = field(default_factory=list)
    plugins_path: list[Any] = field(default_factory=list)
    data: list[Any] = field(default_factory=list)
    src: str = None
    version_header: str = None
    vcredist: str = None
    unix_dirs: bool = False
    vcruntime: bool = False
    system: bool = False
    msapi: bool = False
    dry_run: bool = False
    dst: str = None
    no_repeat: bool = False
    output: str = None
    ace: str = None
    zip: bool = False
    
@dataclass
class SetupFile:
    name: str
    version: list[int]
    version_str: str

def cmp_setup_file(e1: SetupFile, e2: SetupFile):
    v1 = e1.version
    v2 = e2.version
    for a,b in itertools.zip_longest(v1, v2, fillvalue=0):
        if a > b:
            return 1
        elif a < b:
            return -1
    return 0

def get_setup_files(base_path: str, appname: str) -> list[tuple[str, list[int], str]]:
    rx = re.compile('setup[.-]' + appname + '[.-]([0-9.]+)[.]exe')
    files = []
    for n in os.listdir(base_path):
        m = rx.match(n)
        if m:
            version = [int(e) for e in m.group(1).split('.')]
            version_str = m.group(1)
            files.append(SetupFile(n, version, version_str))
    files.sort(key=functools.cmp_to_key(cmp_setup_file))
    return files

def fourints(v):
    cols = v.split('.')
    while len(cols) < 4:
        cols.append('0')
    return ','.join(cols)

def save_text(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(data)

def update_header(header_path, version):
    lines = load_lines(header_path)
    for i, line in enumerate(lines):
        rx = "\\s+".join(['\\s*#\\s*define', '([^ ]*)', '(.*)'])
        m = re.match(rx, line)
        if m:
            n = m.group(1)
            v = m.group(2)
            if n == 'APP_VERSION':
                lines[i] = '#define APP_VERSION "{}"\n'.format(version)
            elif n == 'APP_VERSION_INT':
                lines[i] = '#define APP_VERSION_INT {}\n'.format(fourints(version))
    save_text(header_path, ''.join(lines))

if os.environ.get('DEBUG_MUGIDEPLOY') == "1":
    debug_print = lambda *args, **kwargs: print(*args, **kwargs, file=sys.stderr)
else:
    debug_print = lambda *args: False

def noext_basename(path):
    return os.path.splitext(os.path.basename(path))[0]

class BaseLogger():
    def __init__(self, cmd):
        self._cmd = cmd
        self._src = []
        self._dst = []

    def print_copied(self, src, dst):
        self._src.append(src)
        self._dst.append(dst)

    def flush_copied(self, src_label = "Sources", dst_label = "Collected", abspath = False):
        now = datetime.datetime.now()
        path = os.path.join(os.getcwd(), 'mugideploy.log')
        with open(path, 'a', encoding='utf-8') as f:
            print('# ' + now.strftime('%Y-%m-%d %H:%M:%S'), file=f)
            print('# ' + self._cmd, file=f)
            for src, dst in zip(self._src, self._dst):
                print(f"{src} -> {dst}", file=f)
        self._src = []
        self._dst = []

    def print_info(self, msg):
        pass

    def print_error(self, msg):
        pass

    def print_writen(self, path):
        pass

    def multiple_candidates(self, name, items):
        pass

class Logger(BaseLogger):

    def __init__(self, cmd):
        super().__init__(cmd)

    def print_info(self, msg):
        print(Fore.YELLOW + Style.BRIGHT + msg + Fore.RESET + Style.NORMAL, file=sys.stderr)

    def print_error(self, msg):
        print(Fore.RED + Style.BRIGHT + msg + Fore.RESET, file=sys.stderr)

    def flush_copied(self, src_label = "Sources", dst_label = "Collected", abspath = False):
        print("\n" + src_label, file=sys.stderr)
        for item in self._src:
            print(Fore.GREEN + Style.BRIGHT + item + Fore.RESET + Style.NORMAL, file=sys.stderr)
        print("\n" + dst_label, file=sys.stderr)
        cwd = os.getcwd()
        if abspath:
            getpath = lambda item: item
        else:
            getpath = lambda item: os.path.relpath(item, cwd)
        for item in self._dst:
            print(Fore.GREEN + Style.BRIGHT + getpath(item) + Fore.RESET + Style.NORMAL, file=sys.stderr)
        super().flush_copied(src_label, dst_label, abspath)

    def print_writen(self, path):
        print(Fore.YELLOW + Style.BRIGHT + path + Fore.RESET + Style.NORMAL + " writen", file=sys.stderr)

    def multiple_candidates(self, name, items):
        print(Fore.MAGENTA + "Multiple candidates for " + name + "\n" + Fore.MAGENTA + Style.BRIGHT + "\n".join(items) + Fore.RESET + Style.NORMAL + "\n", file=sys.stderr)

class MutedLogger(BaseLogger):
    def __init__(self, cmd):
        super().__init__(cmd)

class JSONEncoder(json.JSONEncoder):
    def default(self, obj):
        return obj.__dict__    

@dataclass
class Binary:
    name: str
    path: str = None
    dependencies: list[str] = None
    isplugin: bool = False
    dest: str = None

class DataItem:

    (
        APPDATA,

    ) = range(1)

    def __init__(self, path, dest = None, isdir = False):
        self._path = path
        self._dest = dest
        self._isdir = isdir

    def innoSource(self):
        if self._isdir:
            return self._path + "\\*"
        return self._path

    def innoDest(self):
        dest = self._dest
        if dest is None:
            if self._isdir:
                return os.path.join("{app}", self._path)
            return "{app}"
        else:
            if "%appdata%" in dest.lower():
                dest = re.sub("%appdata%", "{userappdata}", dest, 0, re.IGNORECASE)
            if "{app}" or "{userappdata}" in dest.lower():
                pass
            else:
                dest = os.path.join("{app}", dest)
            return dest

    def innoFlags(self):
        dest = self._dest
        if isinstance(dest, int) and dest == self.APPDATA:
            return "ignoreversion createallsubdirs recursesubdirs comparetimestamp"
        else:
            return None

def is_child_path(path, base):
    return os.path.realpath(path).startswith(os.path.realpath(base))

def unique_case_insensitive(paths):
    used = set()
    res = []
    for path in paths:
        if path.lower() not in used:
            res.append(path)
            used.add(path.lower())
    return res

class Resolver:
    def __init__(self, paths, exts):
        binaries = defaultdict(list)
        paths_ = unique_case_insensitive(paths)
        for path in paths_:
            try:
                items = os.listdir(path)
                for item in items:
                    ext_ = os.path.splitext(item)[1].lower()
                    if ext_ not in exts:
                        continue
                    name = item.lower()
                    binaries[name].append(os.path.join(path, item))
            except Exception as e:
                #print(e)
                pass
        for name, items in binaries.items():
            binaries[name] = unique_case_insensitive(items)
        self._binaries = binaries
    
    def resolve(self, name, logger):
        name_ = name.lower()
        if name_ not in self._binaries:
            if name_.startswith('api-ms'):
                return None
            else:
                raise ValueError("{} cannot be found".format(name))
        items = self._binaries[name_]
        if len(items) > 1:
            logger.multiple_candidates(name, items)
        if len(items) < 1:
            raise ValueError("cannot resolve {}".format(name))

        return items[0]

def makedirs(path):
    try:
        os.makedirs(path)
    except:
        pass

def deduplicate(binaries):
    res = []
    names = set()
    for item in binaries:
        name = item.name.lower()
        if name in names:
            continue
        res.append(item)
        names.add(name)
    return res

def get_dependencies(path):
    pe = pefile.PE(path, fast_load=True)
    pe.parse_data_directories(import_dllnames_only=True)

    if not hasattr(pe, 'DIRECTORY_ENTRY_IMPORT'):
        return []
    else:
        res = [name for name in [item.dll.decode('utf-8') for item in pe.DIRECTORY_ENTRY_IMPORT] if name.lower().endswith('.dll')]
        return res

class PEReader:
    def __init__(self):
        path = os.path.join(os.getenv('APPDATA'), "mugideploy", "pe-cache.json")
        makedirs(os.path.dirname(path))
        self._path = path
        self._cache = dict()
        self._changed = False
        
        if os.path.exists(path):
            with open(path) as f:
                self._cache = json.load(f)
        

    def get_dependencies(self, path):
        cache = self._cache
        mtime = os.path.getmtime(path)
        if path in cache and mtime <= cache[path]["mtime"]:
            #print('{} found in cache'.format(path))
            return cache[path]["dependencies"]
        dependencies = get_dependencies(path)
        cache[path] = {"dependencies": dependencies, "mtime": mtime}
        self._changed = True
        return dependencies

    def save(self):
        if not self._changed:
            return
        with open(self._path, "w") as f:
            json.dump(self._cache, f, indent=1)
        
class BinariesPool:
    def __init__(self, paths, resolver: Resolver, config, logger):

        vcruntime = False
        pool: list[Binary] = []

        for path in paths:
            if isinstance(path, str):
                if not os.path.isfile(path):
                    path = resolver.resolve(path, logger)
                pool.append(Binary(os.path.basename(path), path))
            else:
                pool.append(path)

        skip_list = set(['msvcp140.dll','msvcr90.dll'])
        
        reader = PEReader()
        i = 0
        while i < len(pool):
            item = pool[i]
            if item.path is None:
                item.path = resolver.resolve(item.name, logger)
            if item.dependencies is None:
                if item.path is None:
                    item.dependencies = []
                    continue
                dependencies = reader.get_dependencies(item.path)
                for dep in dependencies:
                    if dep.lower().startswith('vcruntime'):
                        vcruntime = True
                item.dependencies = [dep for dep in dependencies if dep.lower() not in skip_list]
                for dll in item.dependencies:
                    if not any(map(lambda item: item.name.lower() == dll.lower(), pool)):
                        pool.append(Binary(dll))
            i += 1
        self._pool = pool
        self._vcruntime = vcruntime

        def is_system(name):
            name = name.lower()
            if name.startswith('vcruntime'):
                return False
            if name.startswith('libssl'):
                return False
            if name.startswith('libcrypto'):
                return False
            if name.startswith('api-ms'):
                return False
            return True

        def file_ext(name):
            return os.path.splitext(name)[1].lower()

        self._system = [name.lower() for name in os.listdir('C:\\windows\\system32') if file_ext(name) == '.dll' and is_system(name)]
        self._msapi = [name.lower() for name in os.listdir('C:\\windows\\system32') if name.lower().startswith('api-ms')] #+ [name.lower() for name in os.listdir('C:\\windows\\system32\\downlevel') if name.lower().startswith('api-ms')]

        reader.save()
    
    def find(self, name) -> Binary | None:
        name = os.path.basename(name).lower()
        for item in self._pool:
            if item.name.lower() == name:
                return item

    def is_system(self, binary):
        if isinstance(binary, str):
            binary = self.find(binary)
        return binary.name.lower() in self._system

    def is_msapi(self, binary):
        if isinstance(binary, str):
            if binary.lower().startswith('api-ms'):
                return True
            binary = self.find(binary)
        return binary.name.lower() in self._msapi or binary.name.lower().startswith('api-ms')

    def is_vcruntime(self, binary):
        if isinstance(binary, str):
            binary = self.find(binary)
        return binary.name.lower().startswith('vcruntime')

    def binaries(self, binaries: list[Binary | str], system = False, msapi = False, vcruntime = True) -> list[Binary]:
        res: list[Binary] = []
        queue = binaries[:]
        found = set()
        while len(queue):
            item = queue.pop(0)
            if isinstance(item, str):
                item = self.find(item)
            if item.name.lower() in found:
                continue
            res.append(item)
            found.add(item.name.lower())
            for name in item.dependencies:
                if not system and name.lower() in self._system:
                    continue
                if not msapi and (name.lower() in self._msapi or name.startswith('api-ms')):
                    continue
                if not vcruntime and name.lower().startswith('vcruntime'):
                    continue
                queue.append(name)
        return res

    def vcruntime(self):
        return self._vcruntime

class PluginsCollectionItem:
    def __init__(self, name, path, base, isdir = False):
        self.name = name
        self.path = path
        self.base = base
        self.isdir = isdir
    def __repr__(self):
        return "PluginsCollectionItem({}, {}, {}, {})".format(self.name, self.path, self.base, self.isdir)

def to_debug_release(files):
    debug = []
    release = []
    while len(files) > 0:
        name = files.pop(0)
        n, e = os.path.splitext(name)
        if n.endswith('4'):
            named = n[:-1] + 'd4' + e
        else:
            named = n + 'd' + e
        #debug_print(name, named)
        if named in files:
            files.pop(files.index(named))
            debug.append(named)
            release.append(name)
        else:
            release.append(name)
    debug_print(debug, release)
    return debug, release

class PluginsCollection:
    def __init__(self, paths, is_debug):
        self._paths = paths
        self._is_debug = is_debug
        collection: dict[str, list[PluginsCollectionItem]] = dict()

        for path in paths:
            for root, dirs, files in os.walk(path):
                base = os.path.basename(root)
                collection[base] = []
                for d in dirs:
                    collection[base].append(PluginsCollectionItem(d, os.path.join(root,d), path, True))

                debug, release = to_debug_release(files)

                if is_debug:
                    files_ = debug
                else:
                    files_ = release

                for f in files_:
                    if os.path.splitext(f)[1].lower() != '.dll':
                        continue
                    plugin_path = os.path.join(root,f)
                    collection[base].append(PluginsCollectionItem(f, plugin_path, path, False))
                    base_ = os.path.splitext(f)[0]
                    collection[base_] = [PluginsCollectionItem(f, plugin_path, path, False)]
                    base_ = os.path.basename(f)
                    collection[base_] = [PluginsCollectionItem(f, plugin_path, path, False)]
        self._collection = collection

    def is_plugin(self, path):
        name = os.path.splitext(os.path.basename(path))[0]
        if name in self._collection:
            item = self._collection[name][0]
            if os.path.samefile(item.path, path):
                return True
        return False

    def binaries(self, names) -> list[Binary]:
        res = []
        i = 0
        names_ = names[:]
        while i < len(names_):
            
            name = names_[i]
            items = self._collection[name]
            for item in items:
                if item.isdir:
                    pass
                else:
                    dest = os.path.dirname(os.path.join('plugins', os.path.relpath(item.path, item.base)))
                    
                    if os.path.basename(dest).lower() in ["debug", "release"]:
                        dest = os.path.dirname(dest)

                    binary = Binary(os.path.basename(item.path), item.path, isplugin=True, dest=dest)
                    res.append(binary)
            i += 1
        return res


def makedirs(path):
    os.makedirs(path, exist_ok=True)

def executable_with_ext(exe):
    if os.path.splitext(exe)[1] == '':
        return exe + '.exe'
    return exe

def write_json(path, obj):
    makedirs(os.path.dirname(path))
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)

def read_json(path):
    try:
        with open(path, "r", encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None

def write_qt_conf(path):
    base = os.path.dirname(path)
    os.makedirs(base, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('[Paths]\nplugins = plugins')

def config_path():
    return os.path.join(os.getcwd(), 'mugideploy.json')

def changelog_path(config):
    if 'src' in config:
        return os.path.join(config.src, 'changelog.json')
    return os.path.join(os.getcwd(), 'changelog.json')

def read_changelog(config):
    path = changelog_path(config)
    try:
        with open(path, "r", encoding='utf-8') as f:
            j = json.load(f)
        return j
    except FileNotFoundError:
        return dict()

def write_changelog(config, changelog):
    write_json(changelog_path(config), changelog)

def update_config_changelog(config, version, message):
    changelog = read_changelog(config)
    changelog[version] = message
    write_changelog(config, changelog)

def makedirs(path):
    os.makedirs(path, exist_ok=True)

def cdup(path, n):
    for _ in range(n):
        path = os.path.dirname(path)
    return path

def split_lines(text):
    return [l.strip() for l in text.split('\n')]

def filter_empty(lines):
    return [l for l in lines if l != '']

def query_plugins_path():
    qmake = shutil.which("qmake")
    if qmake is None:
        return []
    lines = filter_empty(split_lines(subprocess.check_output([qmake, "-query", "QT_INSTALL_PLUGINS"]).decode('utf-8')))
    return lines

def append_list(config, key, values, expand_globs = False):

    if values is None:
        return

    if key not in config:
        config[key] = []

    if not isinstance(config[key], list):
        config[key] = [config[key]]

    if isinstance(values, list):
        values_ = values
    else:
        values_ = [values]

    values__ = []

    for value in values_:
        if expand_globs and glob.has_magic(value):
            values__ += glob.glob(value)
        else:
            values__.append(value)
    
    for value in values__:
        if value not in config[key]:
            config[key].append(value)

def paths_and_globs(items):
    res = []
    if items is None:
        return res
    for item in items:
        if glob.has_magic(item):
            for matched in glob.glob(item):
                res.append(matched)
        else:
            res.append(item)
    return res

def args_to_config(args) -> Config:

    config = Config()

    for n in ['name', 'version', 'output', 'output_dir', 'src', 'dst', 'dry_run', 'ace', 'no_repeat', 'zip', 'unix_dirs']:
        setattr(config, n, getattr(args, n))

    for n in ['data', 'bin', 'plugins', 'plugins_path']:
        items = paths_and_globs(getattr(args, n))
        setattr(config, n, items)

    if config.name is None:
        if len(config.bin) > 0:
            config.name = os.path.splitext(os.path.basename(config.bin[0]))[0]

    for path in query_plugins_path():
        config.plugins_path.append(path)

    debug_print('plugins-path', config.plugins_path)

    return config

def existing(paths):
    for path in paths:
        if os.path.exists(path):
            return path

def cwd_contains_project_file():
    root = os.getcwd()
    for name in os.listdir(root):
        if os.path.splitext(name)[1] == '.pro':
            path = os.path.join(root, name)
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read()
                if 'SOURCES' in text or 'QT' in text:
                    return True
    return False

def has_any_bin(config):
    if 'bin' not in config:
        return False
    if len(config.bin) == 0:
        return False
    return True

def is_amd64_bin(path):
    pe = pefile.PE(path, fast_load=True)
    return pe.FILE_HEADER.Machine == pefile.MACHINE_TYPE['IMAGE_FILE_MACHINE_AMD64']

@dataclass
class ResolveMetaData:
    amd64: bool
    qt: bool
    qt4: bool
    qt5: bool
    qt6: bool
    qt_debug: bool
    gtk: bool
    qt_gui: bool
    vcruntime: bool

def get_search_paths(config, binaries: list[Any]):
    extra_paths = []
    def dirname(path):
        res = os.path.dirname(path)
        if res == '':
            return '.'
        return res
    for binary in binaries:
        if isinstance(binary, str):
            extra_paths.append(dirname(binary))
        elif isinstance(binary, Binary):
            if binary.isplugin:
                continue
            extra_paths.append(dirname(binary.path))
    search_paths = extra_paths + os.environ['PATH'].split(";")
    return search_paths

def resolve_binaries(config: Config, logger: Logger) -> tuple[list[Binary], ResolveMetaData, BinariesPool]:

    if len(config.bin) < 1:
        raise ValueError("Specify binaries please")

    logger.print_info("Resolving imports\n")

    first_bin = config.bin[0]

    if not os.path.isfile(first_bin):
        search_paths = get_search_paths(config, config.bin)
        resolver = Resolver(search_paths, ['.dll', '.exe'])
        first_bin = resolver.resolve(first_bin, logger)

    dependencies = [e.lower() for e in get_dependencies(first_bin)]

    #debug_print('dependencies', dependencies)

    is_gtk = False
    for dep in dependencies:
        if re.match('libgtk.*\\.dll', dep):
            is_gtk = True

    is_amd64 = is_amd64_bin(first_bin)

    is_qt4 = len({'qtcore4.dll', 'qtcored4.dll'}.intersection(dependencies)) > 0

    is_qt5 = len({'qt5core.dll', 'qt5cored.dll', 'qt5widgets.dll', 'qt5widgetsd.dll'}.intersection(dependencies)) > 0

    is_qt6 = len({'qt6core.dll', 'qt6cored.dll', 'qt6widgets.dll', 'qt6widgetsd.dll'}.intersection(dependencies)) > 0

    is_qt = is_qt4 or is_qt5 or is_qt6

    is_qt_gui = len({
        'qtgui4.dll', 'qtguid4.dll',
        'qt5gui.dll', 'qt5guid.dll', 'qt5widgets.dll',
        'qt6gui.dll', 'qt6guid.dll', 'qt6widgets.dll',
    }.intersection(dependencies)) > 0

    is_qt_debug = len({
        'qtcored4.dll',
        'qt5cored.dll',
        'qt6cored.dll',
    }.intersection(dependencies)) > 0

    if is_qt_gui:
        if is_qt5 or is_qt6:
            if is_qt_debug:
                config.plugins += ['styles', 'qwindowsd']
            else:
                config.plugins += ['styles', 'qwindows']

    binaries: list[str] = config.bin

    if is_qt:
        plugins = config.plugins
        collection = PluginsCollection(config.plugins_path, is_qt_debug)
        not_plugins = []
        for binary in binaries:
            if collection.is_plugin(binary):
                plugins.append(noext_basename(binary))
            else:
                not_plugins.append(binary)
        binaries = not_plugins + collection.binaries(plugins)
    else:
        if len(config.plugins) > 0:
            raise ValueError("len(config.plugins) > 0 and not is_qt")

    search_paths = get_search_paths(config, binaries)

    resolver = Resolver(search_paths, ['.dll', '.exe'])

    pool = BinariesPool(binaries, resolver, config, logger)

    meta = ResolveMetaData(amd64=is_amd64, qt=is_qt, qt4=is_qt4, qt5=is_qt5, qt6=is_qt6, qt_gui=is_qt_gui, qt_debug=is_qt_debug, vcruntime=pool.vcruntime(), gtk=is_gtk)
    
    return pool.binaries(binaries, config.system, config.msapi, config.vcruntime), meta, pool

class InnoScript(dict):

    def __init__(self):
        super().__init__()
        self['Setup'] = []
        self['Languages'] = []
        self['Tasks'] = []
        self['Files'] = []
        self['Icons'] = []
        self['Code'] = []
        self['Run'] = []

    def write(self, path):

        def format_dict(d):
            res = []
            for k,v in d.items():
                if k in ['Name','Source','DestDir','Filename','StatusMsg','Parameters','Description','GroupDescription','MessagesFile']:
                    v_ = '"' + v + '"'
                else:
                    v_ = v
                res.append("{}: {}".format(k,v_))
            return "; ".join(res)

        with open_(path, 'w', encoding='CP1251') as f:
            for section, lines in self.items():
                if len(lines) == 0:
                    continue
                f.write("[{}]\n".format(section))
                for line in lines:
                    if isinstance(line, dict):
                       line = format_dict(line)
                    f.write(line + "\n")
                f.write("\n")

def relpath(path, start):
    try:
        return os.path.relpath(path, start)
    except ValueError:
        pass

def inno_script(config: Config, logger, binaries, meta, pool):

    #qt_conf_path = os.path.join(os.getenv('APPDATA'), "mugideploy", "qt.conf")
    qt_conf_path = os.path.join('tmp', "qt.conf")
    
    if meta.qt:
        write_qt_conf(qt_conf_path)

    script = InnoScript()

    def inno_vars(d):
        res = []
        for k,v in d.items():
            res.append('{}={}'.format(k,v))
        return "\n".join(res)

    vars = {
        'AppName': config.name,
        'AppVersion': config.version,
        'DefaultDirName': os.path.join("{commonpf}", config.name),
        'DefaultGroupName': config.name,
        'UninstallDisplayIcon': os.path.join("{app}", binaries[0].name),
        'Compression': 'lzma2',
        'SolidCompression': 'yes',
        'OutputDir': config.output_dir if config.output_dir else '.',
        'OutputBaseFilename': 'setup-' + config.name + '-' + config.version,
        'RestartIfNeededByRun': 'no',
    }

    if meta.amd64:
        vars['ArchitecturesInstallIn64BitMode'] = 'x64'

    script['Setup'].append(inno_vars(vars))

    script['Languages'].append({
        'Name': 'ru',
        'MessagesFile': 'compiler:Languages\\Russian.isl'
    })

    script['Tasks'].append({
        'Name': 'desktopicon',
        'Description': '{cm:CreateDesktopIcon}',
        'GroupDescription': '{cm:AdditionalIcons}'
    })

    def app_dest(dest):
        if dest is None:
            return "{app}"
        else:
            return os.path.join("{app}", dest)

    cwd = os.getcwd()
    for item in binaries:
        path = relpath(item.path, cwd)
        if path is not None and not path.startswith('..'):
            source = path
            #source = item.path
        else:
            source = item.path
        script['Files'].append({
            'Source': source,
            'DestDir': app_dest(item.dest),
            'Flags': 'ignoreversion'
        })

    if len(config.data) > 0:

        items = []

        for item in config.data:
            dst = None
            if isinstance(item, str):
                src = item
            elif isinstance(item, dict):
                src = item['src']
                dst = item['dst']
            elif isinstance(item, list):
                src, dst = item
            
            if glob.has_magic(src):
                files = glob.glob(src)
            else:
                files = [src]

            for item in files:
                isdir = os.path.isdir(src)
                items.append(DataItem(src, dst, isdir))

        item: DataItem
        for item in items:
            item_ = dict()
            item_['Source'] = item.innoSource()
            item_['DestDir'] = item.innoDest()
            flags = item.innoFlags()
            if flags is not None:
                item_['Flags'] = flags
            
            script['Files'].append(item_)
                
    if meta.qt:
        script['Files'].append('Source: "{}"; DestDir: "{}"'.format(qt_conf_path, app_dest(None)))

    script['Icons'].append({
        'Name': os.path.join('{group}', config.name),
        'Filename': os.path.join('{app}', binaries[0].name)
    })

    script['Icons'].append({
        'Name': os.path.join('{commondesktop}', config.name),
        'Filename': os.path.join('{app}', binaries[0].name),
        'Tasks': 'desktopicon'
    })

    if meta.vcruntime and config.vcredist:
        script['Files'].append({'Source': config.vcredist, 'DestDir': '{tmp}'})
        script['Run'].append({
            'Filename': os.path.join("{tmp}", os.path.basename(config.vcredist)),
            'StatusMsg': "Installing Microsoft Visual C++ Redistributable",
            'Parameters': "/quiet /norestart",
        })

    if config.ace:

        # https://stackoverflow.com/questions/35231455/inno-setup-section-run-with-condition
        # https://stackoverflow.com/questions/12951327/inno-setup-check-if-file-exist-in-destination-or-else-if-doesnt-abort-the-ins

        script['Files'].append({'Source':config.ace, 'DestDir': '{tmp}'})

        script['Run'].append({
            'Filename': os.path.join("{tmp}", os.path.basename(config.ace)),
            'StatusMsg': "Installing Access Database Engine",
            'Parameters': "/quiet /norestart",
            'Check': 'ShouldInstallAce'
        })

        script['Code'].append("""function ShouldInstallAce: Boolean;
begin
    Result := Not FileExists(ExpandConstant('{commoncf}\\microsoft shared\\OFFICE14\\ACECORE.DLL'))
end;""")

    script.write(config.output)

def collect(config: Config, logger: Logger, binaries, meta: ResolveMetaData, pool):

    dry_run = config.dry_run
    dest = config.dst

    arch = "win64" if meta.amd64 else "win32"

    if dest is None:
        dest = '%name%-%version%-%arch%'

    base = dest.replace('%name%', config.name).replace('%version%',config.version).replace('%arch%',arch)

    #base = os.path.join(os.getcwd(), "{}-{}-{}".format(config.name, config.version, arch))

    if meta.gtk or config.unix_dirs:
        base_bin = os.path.join(base, 'bin')
    else:
        base_bin = base

    def shutil_copy(src, dst, verbose = True):
        #print("shutil_copy", src, dst)
        if not dry_run:
            #debug_print(src, dst)
            if os.path.realpath(src) == os.path.realpath(dst):
                debug_print("{} == {}".format(src, dst))
                return
            if os.path.isdir(src):
                copy_tree(src, dst, verbose=False)
            elif os.path.isfile(src):
                shutil.copy(src, dst)
            else:
                logger.print_error("{} is not a file nor a directory".format(src))
                return
        if verbose:
            logger.print_copied(src, dst)

    def makedirs(path):
        if not dry_run:
            os.makedirs(path, exist_ok=True)

    def copy_tree(src, dst, verbose = True):
        for root, dirs, files in os.walk(src):
            rel_path = os.path.relpath(root, src)
            dst_ = os.path.join(dst, rel_path)
            makedirs(dst_)
            for f in files:
                shutil_copy(os.path.join(root, f), os.path.join(dst_, f), False)
        if verbose:
            logger.print_copied(src, dst)

    def copy_tree_if(src, dst, cond):
        for root, dirs, files in os.walk(src):
            rel_path = os.path.relpath(root, src)
            dst_ = os.path.join(dst, rel_path)
            makedirs(dst_)
            for f in files:
                if cond(f):
                    #debug_print("copy_tree_if", os.path.join(root, f))
                    shutil_copy(os.path.join(root, f), os.path.join(dst_, f), False)
        logger.print_copied(src, dst)

    
    makedirs(base_bin)

    logger.print_info("Collecting in {} {}".format(base, "(dry_run)" if dry_run else ""))

    qt_conf_path = os.path.join(base_bin, "qt.conf")

    if meta.qt:
        if not dry_run:
            write_qt_conf(qt_conf_path)
        logger.print_copied('<generated>', qt_conf_path)

    #print(binaries)

    for binary in binaries:

        if binary.path is None:
            #print("skip", b.name)
            continue

        if binary.dest is None:
            dest = os.path.join(base_bin, os.path.basename(binary.path))
        else:
            dest = os.path.join(base_bin, binary.dest, os.path.basename(binary.path))

        makedirs(os.path.dirname(dest))

        shutil_copy(binary.path, dest)

    for path in config.data:
        dest = os.path.join(base, os.path.basename(path))
        shutil_copy(path, dest)

    if meta.vcruntime and config.vcruntime:
        dest = os.path.join(base, os.path.basename(config.vcredist))
        shutil_copy(config.vcredist, dest)

    #debug_print("meta.gtk", meta.gtk)

    logger.flush_copied()

    return base

def version_int(version):
    if re.match("^[0-9.]+$", version):
        cols = version.split(".")
        while len(cols) < 4:
            cols.append("0")
        return ",".join(cols[:4])
    return version_int("0.0.0.1")

def find_version_header(config: Config):
    path = config.version_header
    if path is not None:
        return path
    if config.src:
        guesses = [
            os.path.join(config.src, 'version.h'),
            os.path.join(config.src, 'src', 'version.h')
        ]
    else:
        cwd = os.path.realpath('.')
        guesses = [
            os.path.join(cwd, 'version.h'),
            os.path.join('src', 'version.h')
        ]
        if 'build' in os.path.basename(cwd):
            guesses.append(os.path.join(cwd, '..', 'version.h'))
            guesses.append(os.path.join(cwd, '..', 'src', 'version.h'))
    for path in guesses:
        if os.path.exists(path):
            return path
    raise ValueError("version.h not found, please use --version-header or --src")

def find_cmakelists(config):
    if config.src:
        guesses = [
            os.path.join(config.src, 'CMakeLists.txt'),
        ]
    else:
        cwd = os.path.realpath('.')
        guesses = [
            os.path.join(cwd, 'CMakeLists.txt'),
            os.path.join(os.path.dirname(cwd), 'CMakeLists.txt'),
        ]
    for guess in guesses:
        if os.path.exists(guess):
            return guess
    #raise ValueError("CMakeLists.txt not found, please use --src")

def find_inno_compiler():
    return existing([
        os.path.join(os.environ['ProgramFiles(x86)'], 'Inno Setup 6', 'compil32.exe'),
        os.path.join(os.environ['ProgramFiles'], 'Inno Setup 6', 'compil32.exe')
    ])

def get_file_hash(filename, method = 'sha256'):
    h = getattr(hashlib, method)()
    with open(filename,"rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            h.update(byte_block)
        return h.hexdigest()

def zip_dir(config, logger, path):
    parent_dir = os.path.dirname(path)
    zip_path = path + '.zip'
    with zipfile.ZipFile(zip_path, 'w') as zip:
        for root, dirs, files in os.walk(path):
            for f in files:
                abs_path = os.path.join(root, f)
                rel_path = os.path.relpath(abs_path, parent_dir)
                zip.write(abs_path, rel_path)
    logger.print_info("Ziped to {}".format(zip_path))

class PrettyNames:
    def __init__(self):
        self._names = defaultdict(list)

    def __setitem__(self, name, value):
        name_ = name.lower()
        self._names[name_].append(name)

    def __getitem__(self, name):
        name_ = name.lower()
        for name in self._names[name_]:
            b = os.path.splitext(name)[0]
            if b.upper() != b:
                return name
        return self._names[name_][0]

    def names(self, name):
        name_ = name.lower()
        return self._names[name_]


from treelib import Node, Tree
import uuid

class Node:
    def __init__(self, binary):
        self.binary = binary
        self.children: list[Node] = []
        self.uuid = str(uuid.uuid4())

def copy_dep(config: Config, logger: Logger, binaries: list[Binary], meta, pool):
    dst = config.dst
    binary: Binary
    for binary in binaries:
        file_dst = os.path.join(dst, binary.name)
        if binary.isplugin:
            continue
        if os.path.isfile(file_dst):
            continue
        if not config.dry_run:
            shutil.copy(binary.path, file_dst)
        logger.print_copied(binary.path, file_dst)
    logger.flush_copied("Source", "Destination", os.path.isabs(dst))

def print_tree(config: Config, binaries: list[Binary], meta, pool):

    def find(name):
        for bin in binaries:
            if bin.name.lower() == name.lower():
                return bin

    added = set()

    def add_children(node: Node):
        binary = node.binary
        for dep in binary.dependencies:
            bin = find(dep)

            if bin is None:
                #print("{} not found".format(dep), file=sys.stderr)
                continue

            if config.no_repeat:
                if bin.name.lower() in added:
                    continue
            added.add(bin.name.lower())

            child = Node(bin)
            node.children.append(child)
            add_children(child)
            
    root = Node(None)

    for path in config.bin:
        name = os.path.basename(path)
        bin = find(name)

        if config.no_repeat:
            if bin.name.lower() in added:
                continue
        added.add(bin.name.lower())

        node = Node(bin)
        add_children(node)
        root.children.append(node)

    tree = Tree()

    def build_tree(node: Node):
        child: Node
        for child in node.children:
            tree.create_node(child.binary.name, child.uuid, parent=node.uuid)
            build_tree(child)

    tree.create_node("root", root.uuid)
    build_tree(root)

    with open_(config.output, "w", encoding='utf-8') as f:
        for child in root.children:
            subtree = tree.subtree(child.uuid)
            print(subtree.show(stdout=False), file=f)

def write_graph(config, logger, binaries, meta, pool: BinariesPool):

    names = PrettyNames()

    deps = set()
    for binary in binaries:
        if binary.path is None:
            print("binary {} has no path".format(binary.name))
            continue

        name = binary.name

        name1 = binary.name
        name2 = os.path.basename(binary.path)

        names[name1] = name
        names[name2] = name

        for dep_binary in binary.dependencies:
            deps.add((binary.name.lower(), dep_binary.lower()))
            names[dep_binary] = dep_binary
    
    digraph = "digraph G {\nnode [shape=rect]\n" + "\n".join(['    "{}" -> "{}"'.format(names[name], names[dependancy]) for name, dependancy in deps]) + "\n}\n"

    with open_(config.output, 'w', encoding='utf-8') as f:
        f.write(digraph)

def clear_cache():
    path = os.path.join(os.getenv('APPDATA'), "mugideploy", "pe-cache.json")
    os.remove(path)

def parse_cmakelists_for_version(config):
    path = find_cmakelists(config)
    if path is None:
        return
    rx = re.compile('project\\(.*VERSION\\s+([^\\s]+)', re.IGNORECASE)
    with open(path, encoding='utf-8') as f:
        for line in f:
            m = rx.search(line)
            if m:
                config.version = m.group(1)
                break

def load_lines(path):
    with open(path, encoding='utf-8') as f:
        return list(f)

def parse_header(header_path):
    lines = load_lines(header_path)
    for i, line in enumerate(lines):
        rx = "\\s+".join(['\\s*#\\s*define', '([^ ]*)', '(.*)'])
        m = re.match(rx, line)
        if m:
            n = m.group(1)
            v = m.group(2)
            if n == 'APP_VERSION':
                version = v.strip().replace('"', '')
                return version

def parse_header_for_version(config):
    cwd = os.getcwd()
    header_path = os.path.join(cwd, 'version.h')
    
    if os.path.isfile(header_path):
        debug_print('version header found')
        version = parse_header(header_path)
        if version is not None:
            config.version = version
            debug_print('APP_VERSION found in header, value', version)
    else:
        debug_print('version header does not exist', header_path)

class HelpFormatter(argparse.HelpFormatter):
    def __init__(self, prog, indent_increment = 2, max_help_position = 24, width = None):
        super().__init__(prog, indent_increment, max_help_position = 50, width = 120)

def main():

    colorama_init()

    parser = argparse.ArgumentParser(prog='mugideploy', formatter_class=HelpFormatter)

    parser.add_argument('command', choices=['json', 'list', 'graph', 'tree', 'collect', 'inno-script', 'copy-dep', 'clear-cache'])
    
    parser.add_argument('--name', help='App name')
    parser.add_argument('--version', help='App version')
    
    parser.add_argument('--bin', nargs='+', help='Binaries (dlls, exes)')
    parser.add_argument('--data', nargs='+', help='Path to data dirs and files')
    parser.add_argument('--plugins', nargs='+', help='Plugin names')
    parser.add_argument('--plugins-path', nargs='+', help='Path to plugins')

    parser.add_argument('--dst', help="Destination path or path template")

    parser.add_argument('--vcredist', help='Path to Microsoft Visual C++ Redistributable')
    parser.add_argument('--ace', help='Path to Access Database Engine')
    
    parser.add_argument('--system', action='store_true', help='Include system dlls')
    parser.add_argument('--vcruntime', action='store_true', help='Include vcruntime dlls')
    parser.add_argument('--msapi', action='store_true', help='Include msapi dlls')
    
    # https://en.wikipedia.org/wiki/Access_Database_Engine
    # ace14 https://download.microsoft.com/download/3/5/C/35C84C36-661A-44E6-9324-8786B8DBE231/accessdatabaseengine_X64.exe

    parser.add_argument("-q", "--quiet", action='store_true', help='Do not print additional info')
    parser.add_argument('--unix-dirs', action='store_true', help='bin var etc dirs')

    parser.add_argument('--src', help='Path to sources')
    parser.add_argument('--version-header', help='Path to version header')

    parser.add_argument('--dry-run', action='store_true', help="Do not copy files")
    parser.add_argument('--zip', action='store_true', help='Zip collected')
    parser.add_argument('--output-dir', help='Inno setup script output dir')

    # find, graph, list, inno-script
    parser.add_argument('-o','--output', help='Path to save file')
    # tree
    parser.add_argument("--no-repeat", action='store_true', help='Print each dll once')

    args, extraArgs = parser.parse_known_args()

    if args.command == 'clear-cache':
        clear_cache()
        return

    cmd = ' '.join([f'"{v}"' if ' ' in v else v for v in sys.argv])
    if args.quiet:
        logger = MutedLogger(cmd)
    else:
        logger = Logger(cmd)

    debug_print(args)
    for arg in extraArgs:
        if os.path.splitext(arg)[1].lower() in ['.dll', '.exe']:
            logger.print_info("unexpected argument {}, did you mean --bin {}?".format(arg, arg))
        else:
            logger.print_info("unexpected argument {}".format(arg))

    config: Config = args_to_config(args)

    if args.command == 'copy-dep':
        if args.dst is None:
            raise ValueError("specify --dst")
    
    if config.version is None:
        parse_cmakelists_for_version(config)

    if config.version is None:
        parse_header_for_version(config)

    if config.version is None:
        config.version = '0.0.1'

    binaries, meta, pool = resolve_binaries(config, logger)

    if args.command == 'json':

        with open_(config.output, 'w', encoding='utf-8') as f:
            json.dump(binaries, f, ensure_ascii=False, indent=1, cls=JSONEncoder)

    elif args.command == 'list':

        with open_(config.output, 'w', encoding='utf-8') as f:
            for binary in binaries:
                print(binary.path, file=f)

    elif args.command == 'tree':

        print_tree(config, binaries, meta, pool)

    elif args.command == 'copy-dep':

        copy_dep(config, logger, binaries, meta, pool)

    elif args.command == 'graph':

        write_graph(config, logger, binaries, meta, pool)

    elif args.command == 'collect':

        path = collect(config, logger, binaries, meta, pool)
        if config.zip:
            zip_dir(config, logger, path)

    elif args.command == 'inno-script':

        inno_script(config, logger, binaries, meta, pool)
        
    elif args.command == 'clear-cache':

        clear_cache()

if __name__ == "__main__":
    main()
