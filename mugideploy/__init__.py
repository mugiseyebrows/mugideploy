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
from dataclasses import dataclass
from collections import defaultdict
import zipfile
import urllib.parse

# TODO do not store (optionally) plugins-path
# TODO update --license
# TODO collect --zip

MSYSTEMS = ['MINGW32', 'MINGW64', 'UCRT64', 'CLANG64', 'MSYS2']

def debug_print_on(*args):
    print(*args)

def debug_print_off(*args):
    pass

# set DEBUG_MUGIDEPLOY=1

if 'DEBUG_MUGIDEPLOY' in os.environ and os.environ['DEBUG_MUGIDEPLOY'] == "1":
    debug_print = debug_print_on
else:
    debug_print = debug_print_off

class Logger():

    def __init__(self):
        self._src = []
        self._dst = []

    def print_info(self, msg):
        print(Fore.YELLOW + Style.BRIGHT + msg + Fore.RESET + Style.NORMAL)

    def print_error(self, msg):
        print(Fore.RED + Style.BRIGHT + msg + Fore.RESET)

    def print_copied(self, src, dst):
        if src is not None:
            self._src.append(src)

        if dst is not None:
            self._dst.append(dst)

    def flush_copied(self):
        print("\nSources")
        for item in self._src:
            print(Fore.GREEN + Style.BRIGHT + item + Fore.RESET + Style.NORMAL)
        print("\nCollected")
        cwd = os.getcwd()
        for item in self._dst:
            print(Fore.GREEN + Style.BRIGHT + os.path.relpath(item, cwd) + Fore.RESET + Style.NORMAL)
        self._src = []
        self._dst = []

    def print_writen(self, path):
        print(Fore.YELLOW + Style.BRIGHT + path + Fore.RESET + Style.NORMAL + " writen")

    def multiple_candidates(self, name, items):
        print(Fore.MAGENTA + "Multiple candidates for " + name + "\n" + Fore.MAGENTA + Style.BRIGHT + "\n".join(items) + Fore.RESET + Style.NORMAL + "\n")



class JSONEncoder(json.JSONEncoder):
    def default(self, obj):
        return obj.__dict__    

@dataclass
class Binary:
    name: str
    path: str = None
    dependencies: list[object] = None
    isplugin: bool = False
    dest: str = None

class DataItem:

    (
        APPDATA,

    ) = range(1)

    def __init__(self, path, dest = None):
        self.path = path
        self._dest = dest

    def innoDest(self, app):
        dest = self._dest
        
        if dest is None:
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
    return list({v.lower(): v for v in paths}.values())

class Resolver:
    def __init__(self, paths, exts, msys_root):
        binaries = defaultdict(list)
        for path in unique_case_insensitive(paths):
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
        self._msys_root = msys_root
    
    def resolve(self, name, logger):
        name_ = name.lower()
        if name_ not in self._binaries:
            raise ValueError("{} cannot be found".format(name))
        items = self._binaries[name_]
        if len(items) > 1:
            msys_root = self._msys_root
            if msys_root is not None:
                
                items_ = [item for item in items if is_child_path(item, msys_root)]

                debug_print('filtered', items, items_)
                if len(items_) > 1:
                    logger.multiple_candidates(name, items_)
                elif len(items_) == 1:
                    return items_[0]
                else:
                    debug_print('{} not found in {}'.format(name_, msys_root))

            logger.multiple_candidates(name, items)
            #print("multiple choises for {}:\n{}\n".format(name, "\n".join(items)))
        return items[0]

def makedirs(path):
    try:
        os.makedirs(path)
    except:
        pass

def get_dependencies(path):
    pe = pefile.PE(path, fast_load=True)
    pe.parse_data_directories(import_dllnames_only=True)

    debug_print('pefile for {}'.format(path))

    if not hasattr(pe, 'DIRECTORY_ENTRY_IMPORT'):
        print('{} has no DIRECTORY_ENTRY_IMPORT'.format(path))
        return []
    else:
        return [name for name in [item.dll.decode('utf-8') for item in pe.DIRECTORY_ENTRY_IMPORT] if name.lower().endswith('.dll')]

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
    def __init__(self, paths, resolver, logger):
        vcruntime = False
        pool = [Binary(os.path.basename(path), path) if isinstance(path, str) else path for path in paths]
        i = 0
        reader = PEReader()
        while i < len(pool):
            item = pool[i]
            if item.path is None:
                item.path = resolver.resolve(item.name, logger)
            if item.dependencies is None:
                dependencies = reader.get_dependencies(item.path)
                for dep in dependencies:
                    if dep.lower().startswith('vcruntime'):
                        vcruntime = True
                item.dependencies = [dep for dep in dependencies if dep.lower() not in ['msvcp140.dll','msvcr90.dll','vcruntime140.dll','vcruntime140_1.dll']]
                for dll in item.dependencies:
                    if dll.lower().startswith('api-ms-win'):
                        continue
                    if not any(map(lambda item: item.name.lower() == dll.lower(), pool)):
                        pool.append(Binary(dll))
            i += 1
        self._pool = pool
        self._vcruntime = vcruntime
        self._system = [name.lower() for name in os.listdir('C:\\windows\\system32') if os.path.splitext(name)[1].lower() == '.dll' and name not in ['libssl-1_1-x64.dll', 'libcrypto-1_1-x64.dll']]
        reader.save()
    
    def binaries(self, system = False):
        if system:
            return self._pool[:]
        return [item for item in self._pool if item.name.lower() not in self._system]

    def vcruntime(self):
        return self._vcruntime

class PluginsCollectionItem:
    def __init__(self, name, path, base, isdir = False):
        self.name = name
        self.path = path
        self.base = base
        self.isdir = isdir

class PluginsCollection:
    def __init__(self, paths):
        self._paths = paths
        collection = dict()

        aliases = {
            'qsqlite': 'sqlite',
            'qsqlmysql': 'mysql',
            'qsqlodbc': 'odbc',
            'qsqlpsql': 'psql',
            'qsqlite4': 'sqlite',
            'qsqlmysql4': 'mysql',
            'qsqlodbc4': 'odbc',
            'qsqlpsql4': 'psql',
        }

        for path in paths:
            for root, dirs, files in os.walk(path):
                base = os.path.basename(root)
                """
                if base in collection:
                    print('error: {} in collection'.format(base))
                """
                collection[base] = []
                for d in dirs:
                    collection[base].append(PluginsCollectionItem(d, os.path.join(root,d), path, True))
                for f in files:
                    """
                    if f.lower().endswith('d.dll'):
                        continue
                    """
                    if os.path.splitext(f)[1].lower() != '.dll':
                        continue
                    plugin_path = os.path.join(root,f)
                    collection[base].append(PluginsCollectionItem(f, plugin_path, path, False))
                    base_ = os.path.splitext(f)[0]
                    collection[base_] = [PluginsCollectionItem(f, plugin_path, path, False)]
                    if base_ in aliases:
                        collection[aliases[base_]] = [PluginsCollectionItem(f, plugin_path, path, False)]
                    base_ = os.path.basename(f)
                    collection[base_] = [PluginsCollectionItem(f, plugin_path, path, False)]
        self._collection = collection

    def binaries(self, names):
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




"""
sys.path.insert(0, os.getcwd())
try:
    from version import main as version_main
except ImportError as e:
    pass
"""

def makedirs(path):
    try:
        os.makedirs(path)
    except OSError:
        pass

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


#print(args); exit(0)

def write_qt_conf(path):
    base = os.path.dirname(path)
    makedirs(base)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('[Paths]\nplugins = plugins')

def config_path():
    return os.path.join(os.getcwd(), 'mugideploy.json')

def changelog_path(config):
    if 'src' in config:
        return os.path.join(config['src'], 'changelog.json')
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

def update_changelog(config, version, message):
    changelog = read_changelog(config)
    changelog[version] = message
    write_changelog(config, changelog)

def guess_app_and_version(config):
    if 'version' not in config:
        config['version'] = "1.0"
    if 'app' not in config and 'bin' in config:
        config['app'] = os.path.splitext(os.path.basename(config['bin'][0]))[0]

def makedirs(path):
    try:
        os.makedirs(path)
    except:
        pass

def cdup(path, n):
    for _ in range(n):
        path = os.path.dirname(path)
    return path

def guess_plugins_path():
    qmake = shutil.which("qmake")
    if qmake is None:
        return None
    path = os.path.join(cdup(qmake, 2), "plugins")
    if os.path.exists(path):
        return path

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

def set_config_value(config, key, args, default = None):

    value = getattr(args, key)

    if value is not None:
        config[key] = value

    if config.get(key) is None and default is not None:
        config[key] = default


def update_config(config, args):

    global_config = GlobalConfig()
    global_config.update(args)
    global_config.push(config)
    global_config.save()

    debug_print('config', config)

    set_config_value(config, 'app', args)

    set_config_value(config, 'version', args, '0.0.1')

    set_config_value(config, 'toolchain', args)

    if args.msystem:
        config['msystem'] = args.msystem

    if args.src:
        config['src'] = args.src

    if args.version_header:
        config['version_header'] = args.version_header

    config['no_vcredist'] = args.no_vcredist
    config['unix_dirs'] = args.unix_dirs

    if args.data is not None:

        items = []

        for item in args.data:
            if glob.has_magic(item):
                for f in glob.glob(item):
                    items.append(f)
            else:
                items.append(item)

        append_list(config, 'data', items)

    append_list(config, 'bin', getattr(args, 'bin'), expand_globs = True)

    append_list(config, 'plugins', getattr(args, 'plugins'))

    append_list(config, 'plugins-path', args.plugins_path)

    if 'plugins-path' not in config:
        config['plugins-path'] = []

    append_list(config, 'plugins-path', guess_plugins_path())

    debug_print('plugins-path', config['plugins-path'])

    if config.get('app') is None or config['app'] == 'untitled':
        name = 'untitled'
        if has_any_bin(config):
            first_bin = config['bin'][0]
            name = os.path.splitext(os.path.basename(first_bin))[0]
        config['app'] = name
    
    if has_any_bin(config):
        first_bin = os.path.realpath(config['bin'][0]).lower()

        if config.get('msys_root') is None:
            if first_bin.startswith('c:\\msys64'):
                config['msys_root'] = 'C:\\msys64'

        if config.get('msystem') is None and config.get('msys_root') is not None:
            for msystem in MSYSTEMS:
                path = os.path.join(config['msys_root'], msystem).lower()
                #debug_print('path',path)
                if first_bin.startswith(path):
                    config['msystem'] = msystem
                    break
        debug_print('first_bin', first_bin)
        debug_print('msys_root', config.get('msys_root'))
        debug_print('msystem', config.get('msystem'))

def without(obj, keys):
    return {k:v for k,v in obj.items() if k not in keys}

def write_config(config):
    write_json(config_path(), without(config, GlobalConfig.keys))

def read_config():
    config = read_json(config_path())
    if config is None:
        config = dict()
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
    if len(config['bin']) == 0:
        return False
    return True

def is_qt_app(config):

    if not has_any_bin(config):
        return cwd_contains_project_file()

    first_bin = config['bin'][0]
    if not os.path.exists(first_bin):
        return cwd_contains_project_file()

    dependencies = [e.lower() for e in get_dependencies(first_bin)]
    return len({'qt5core.dll','qt5cored.dll','qtcore4.dll','qtcored4.dll'}.intersection(dependencies)) > 0

def test_amd64(path):
    pe = pefile.PE(path, fast_load=True)
    return pe.FILE_HEADER.Machine == pefile.MACHINE_TYPE['IMAGE_FILE_MACHINE_AMD64']

@dataclass
class ResolveMetaData:
    amd64: bool
    qt: bool
    qt4: bool
    qt5: bool
    qt_debug: bool
    gtk: bool
    qt_gui: bool
    vcruntime: bool

def resolve_binaries(logger, config):

    if 'bin' not in config:
        logger.print_error('Specify binaries please')
        return

    logger.print_info("Resolving imports\n")

    dependencies = [e.lower() for e in get_dependencies(config['bin'][0])]

    debug_print('dependencies', dependencies)

    is_gtk = False
    for dep in dependencies:
        if re.match('libgtk.*\\.dll', dep):
            is_gtk = True

    is_amd64 = test_amd64(config['bin'][0])

    is_qt4 = len({'qtcore4.dll', 'qtcored4.dll'}.intersection(dependencies)) > 0

    is_qt5 = len({'qt5core.dll', 'qt5cored.dll', 'qt5widgets.dll'}.intersection(dependencies)) > 0

    is_qt = is_qt4 or is_qt5

    is_qt_gui = len({
        'qtgui4.dll', 'qtguid4.dll',
        'qt5gui.dll', 'qt5guid.dll', 'qt5widgets.dll'
    }.intersection(dependencies)) > 0

    is_qt_debug = len({
        'qtcored4.dll',
        'qt5cored.dll',
    }.intersection(dependencies)) > 0

    if is_qt_gui and is_qt5:
        if 'plugins' not in config:
            config['plugins'] = []
        if is_qt_debug:
            config['plugins'] += ['qwindowsvistastyled', 'qwindowsd']
        else:
            config['plugins'] += ['qwindowsvistastyle', 'qwindows']

    binaries = config['bin']

    if 'plugins' in config and len(config['plugins']):
        collection = PluginsCollection(config['plugins-path'])
        binaries += collection.binaries(config['plugins'])

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

    search_paths = os.environ['PATH'].split(";") + extra_paths

    debug_print(config)

    if config.get('msystem') is not None:
        
        extra_paths = [
            os.path.join(config['msys_root'], config['msystem'].lower(), 'bin')
        ]
        search_paths += extra_paths

    #print(search_paths)

    resolver = Resolver(search_paths, ['.dll', '.exe'], config.get('msys_root'))

    if is_gtk:
        helpers = [resolver.resolve(name, logger) for name in ['gspawn-win64-helper.exe', 'gspawn-win64-helper-console.exe']]
        config['bin'] += helpers

    pool = BinariesPool(binaries, resolver, logger)

    meta = ResolveMetaData(amd64=is_amd64, qt=is_qt, qt4=is_qt4, qt5=is_qt5, qt_gui=is_qt_gui, qt_debug=is_qt_debug, vcruntime=pool.vcruntime(), gtk=is_gtk)
    debug_print(meta)
    return pool.binaries(), meta

def bump_version(config, args, logger):

    index = ['bump-major', 'bump-minor', 'bump-fix'].index(args.command)

    #print(config['version'])
    version = [int(e) for e in config['version'].split(".")]
    version[index] += 1
    config['version'] = ".".join([str(e) for e in version])

    if args.changelog is not None:
        update_changelog(config, config['version'], args.changelog)

    write_config(config)
    run_version_script(config, logger)

class InnoScript(dict):

    def __init__(self):
        super().__init__()
        self['Setup'] = []
        self['Tasks'] = []
        self['Files'] = []
        self['Icons'] = []
        self['Code'] = []
        self['Run'] = []

    def write(self, path):

        def format_dict(d):
            res = []
            for k,v in d.items():
                if k in ['Name','Source','DestDir','Filename','StatusMsg','Parameters','Description','GroupDescription']:
                    v_ = '"' + v + '"'
                else:
                    v_ = v
                res.append("{}: {}".format(k,v_))
            return "; ".join(res)

        with open(path, 'w', encoding='CP1251') as f:
            for section, lines in self.items():
                if len(lines) == 0:
                    continue
                f.write("[{}]\n".format(section))
                for line in lines:
                    if isinstance(line, dict):
                       line = format_dict(line)
                    f.write(line + "\n")
                f.write("\n")

def inno_script(config, logger, binaries, meta):

    #qt_conf_path = os.path.join(os.getenv('APPDATA'), "mugideploy", "qt.conf")
    qt_conf_path = os.path.join('tmp', "qt.conf")
    
    if meta.qt:
        makedirs(os.path.dirname(qt_conf_path))
        write_qt_conf(qt_conf_path)

    script = InnoScript()

    def inno_vars(d):
        res = []
        for k,v in d.items():
            res.append('{}={}'.format(k,v))
        return "\n".join(res)

    vars = {
        'AppName': config["app"],
        'AppVersion': config["version"],
        'DefaultDirName': os.path.join("{commonpf}", config["app"]),
        'DefaultGroupName': config["app"],
        'UninstallDisplayIcon': os.path.join("{app}", binaries[0].name),
        'Compression': 'lzma2',
        'SolidCompression': 'yes',
        'OutputDir': '.',
        'OutputBaseFilename': config["app"] + '-' + config["version"]
    }

    if meta.amd64:
        vars['ArchitecturesInstallIn64BitMode'] = 'x64'

    script['Setup'].append(inno_vars(vars))

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

    for item in binaries:
        script['Files'].append({
            'Source': item.path,
            'DestDir': app_dest(item.dest),
            'Flags': 'ignoreversion'
        })

    if 'data' in config:

        items = []

        for item in config['data']:
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
                items.append(DataItem(src, dst))

        for item in items:
            item_ = dict()
            item_['Source'] = item.path
            item_['DestDir'] = item.innoDest(config['app'])
            flags = item.innoFlags()
            if flags is not None:
                item_['Flags'] = flags
            
            script['Files'].append(item_)
                
        if meta.qt:
            script['Files'].append('Source: "{}"; DestDir: "{}"'.format(qt_conf_path, app_dest(None)))

        script['Icons'].append({
            'Name': os.path.join('{group}', config["app"]),
            'Filename': os.path.join('{app}', binaries[0].name)
        })

        script['Icons'].append({
            'Name': os.path.join('{commondesktop}', config["app"]),
            'Filename': os.path.join('{app}', binaries[0].name),
            'Tasks': 'desktopicon'
        })

    if meta.vcruntime and config['no_vcredist'] == False:

        if meta.amd64:
            vcredist = config['vcredist64']
        else:
            vcredist = config['vcredist32']

        script['Files'].append({'Source':vcredist, 'DestDir': '{tmp}', 'Flags': 'dontcopy'})

        script['Code'].append("""function VC2017RedistNeedsInstall: Boolean;
var 
  Version: String;
begin
  if RegQueryStringValue(HKEY_LOCAL_MACHINE,
       'SOFTWARE\\Microsoft\\VisualStudio\\14.0\\VC\\Runtimes\\x64', 'Version', Version) then
  begin
    // Is the installed version at least 14.14 ? 
    Log('VC Redist Version check : found ' + Version);
    Result := (CompareStr(Version, 'v14.14.26429.03')<0);
  end
  else 
  begin
    // Not even an old version installed
    Result := True;
  end;
  if (Result) then
  begin
    ExtractTemporaryFile('{}');
  end;
end;""".format(os.path.basename(vcredist)))

        script['Run'].append({
            'Filename': os.path.join("{tmp}", os.path.basename(vcredist)),
            'StatusMsg': "Installing Microsoft Visual C++ 2015-2019 Redistributable (x64) - 14.29.30037",
            'Parameters': "/quiet",
            'Check': 'VC2017RedistNeedsInstall',
            'Flags': 'waituntilterminated'
        })

    path = os.path.join(os.getcwd(), 'setup.iss')
    script.write(path)

def collect(config, logger, binaries, meta, dry_run, dest, skip):

    arch = "win64" if meta.amd64 else "win32"

    base = dest.replace('%app%', config["app"]).replace('%version%',config["version"]).replace('%arch%',arch)

    #base = os.path.join(os.getcwd(), "{}-{}-{}".format(config["app"], config["version"], arch))

    if meta.gtk or config['unix_dirs']:
        base_bin = os.path.join(base, 'bin')
    else:
        base_bin = base

    def shutil_copy(src, dst, verbose = True):
        if not dry_run:
            #debug_print(src, dst)
            if os.path.basename(src) in skip:
                return
            shutil.copy(src, dst)
        if verbose:
            logger.print_copied(src, dst)

    def copy_tree(src, dst):
        for root, dirs, files in os.walk(src):
            rel_path = os.path.relpath(root, src)
            dst_ = os.path.join(dst, rel_path)
            makedirs(dst_)
            for f in files:
                shutil_copy(os.path.join(root, f), os.path.join(dst_, f), False)
        logger.print_copied(src, dst)

    def copy_tree_if(src, dst, cond):
        for root, dirs, files in os.walk(src):
            rel_path = os.path.relpath(root, src)
            dst_ = os.path.join(dst, rel_path)
            makedirs(dst_)
            for f in files:
                if cond(f):
                    shutil_copy(os.path.join(root, f), os.path.join(dst_, f), False)
        logger.print_copied(src, dst)

    if not dry_run:
        makedirs(base_bin)

    logger.print_info("Collecting in {} {}".format(base, "(dry_run)" if dry_run else ""))

    qt_conf_path = os.path.join(base_bin, "qt.conf")

    if meta.qt and "qt.conf" not in skip:
        logger.print_copied(None, qt_conf_path)
        if not dry_run:
            write_qt_conf(qt_conf_path)
            
    #print(binaries)

    for b in binaries:
        if b.dest is None:
            dest = os.path.join(base_bin, os.path.basename(b.path))
        else:
            dest = os.path.join(base_bin, b.dest, os.path.basename(b.path))

        if not dry_run:
            makedirs(os.path.dirname(dest))

        shutil_copy(b.path, dest)

    # changelog
    if 'src' in config:
        path = os.path.join(config['src'], 'changelog.json')
    else:
        path = os.path.join(os.getcwd(), 'changelog.json')
    if os.path.isfile(path):
        dest = os.path.join(base, 'changelog.json')
        shutil_copy(path, dest)
        
    if 'data' in config:
        for path in config['data']:
            if glob.has_magic(path):
                files = glob.glob(path)
            else:
                files = [path]
            for item in files:
                dest = os.path.join(base, os.path.basename(item))
                shutil_copy(item, dest)

    if meta.vcruntime and config['no_vcredist'] == False:

        if meta.amd64:
            vcredist = config['vcredist64']
        else:
            vcredist = config['vcredist32']

        dest = os.path.join(base, os.path.basename(vcredist))
        shutil_copy(vcredist, dest)

    if meta.gtk:
        msys_root = config['msys_root']

        msystem = config['msystem'].lower()

        msystem_base = os.path.join(msys_root, msystem)

        extra = [
            os.path.join(msystem_base, 'share', 'icons', 'Adwaita'),
            os.path.join(msystem_base, 'lib', 'gdk-pixbuf-2.0', '2.10.0'),
            os.path.join(msystem_base, 'share', 'glib-2.0')
        ]

        for src in extra:
            dst = os.path.join(base, os.path.relpath(src, msystem_base))
            copy_tree(src, dst)

        src = os.path.join(msystem_base, 'share', 'locale')
        dst = os.path.join(base, os.path.relpath(src, msystem_base))

        name = os.path.splitext(os.path.basename(config['bin'][0]))[0]

        # locales
        copy_tree_if(src, dst, lambda f: os.path.splitext(f)[0] == name)

    logger.flush_copied()

    return base

def inno_compile(config, logger):
    
    path = "setup.iss"
    
    compiler = config['inno_compiler']

    if compiler is None:
        logger.print_error("compil32.exe not found")
        return

    subprocess.run([compiler, '/cc', path], cwd = os.getcwd())

def build(logger, config):

    toolchain = config.get('toolchain')

    if toolchain is None:
        if shutil.which('cl'):
            toolchain = 'vs'
        elif shutil.which('mingw32-make'):
            toolchain = 'mingw'
        else:
            logger.print_error("Specify toolchain please")
            return

    logger.print_info("Using {} toolchain".format(toolchain))

    commands = None

    cwd = os.getcwd()
    is_qt = is_qt_app(config)

    if toolchain in ['mingw', 'mingw32']:

        commands = [["mingw32-make", "-j4", "release"]]
        if is_qt:
            commands = [["qmake"]] + commands

    elif toolchain in ['vs', 'vc']:

        jom = shutil.which("jom")
        nmake = shutil.which("nmake")
        commands = [[jom if jom is not None else nmake, "release"]]
        if is_qt:
            commands = [["qmake"]] + commands

    elif toolchain == 'cmake':
        
        cwd = os.path.join(cwd, "build")
        makedirs(cwd)
        commands = [["cmake", ".."],["cmake","--build",".","--config","Release"]]

    if commands is None:
        print("unknown toolchain {}".format(toolchain))
    else:
        for command in commands:
            subprocess.run(command, cwd=cwd)

def version_int(version):
    return ",".join((version.split(".") + ["0"] * 3)[:4])

def run_version_script(config, logger):
    path = os.path.join(os.getcwd(), 'version.py')
    if os.path.exists(path):
        version = SourceFileLoader("version", path).load_module()
        version.main()
    else:
        if 'version_header' in config:
            path = config['version_header']
        elif 'src' in config:
            path = os.path.join(config['src'], 'version.h')
        else:
            error = '--version-header or --src required'
            logger.print_error(error)
            raise ValueError(error)
        if not os.path.isfile(path):
            error = '{} not exist'.format(path)
            logger.print_error(error)
            raise ValueError(error)
        with open(path, 'w', encoding='utf-8') as f:
            version = config['version']
            f.write("#define VERSION \"{}\"\n".format(version))
            f.write("#define VERSION_INT {}\n".format(version_int(version)))

def find_inno_compiler():
    return existing([
        os.path.join(os.environ['ProgramFiles(x86)'], 'Inno Setup 6', 'compil32.exe'),
        os.path.join(os.environ['ProgramFiles'], 'Inno Setup 6', 'compil32.exe')
    ])
    
class GlobalConfig:

    keys = ['vcredist32', 'vcredist64', 'inno_compiler', 'msys_root']

    def __init__(self):
        data = dict()
        try:
            data = read_json(self._path())
            if data is None:
                data = dict()
        except Exception:
            pass
        if data.get('inno_compiler') is None:
            data['inno_compiler'] = find_inno_compiler()
        self._data = data
        self._changed = False

    def _path(self):
        return os.path.join(os.getenv('APPDATA'), "mugideploy", "mugideploy.json")

    def update(self, args):
        for k in self.keys:
            if hasattr(args, k):
                v = getattr(args, k)
                if v is not None:
                    self._data[k] = v
                    self._changed = True
        debug_print('global_config', self._data)

    def push(self, config):
        for k, v in self._data.items():
            config[k] = v

    def save(self):
        if len(self._data) == 0 or not self._changed:
            return
        write_json(self._path(), self._data)

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

def write_graph(binaries, meta, output, skip_system, show_graph):

    names = PrettyNames()

    if skip_system:
        skip = [name.lower() for name in os.listdir("C:\\windows\\system32") if os.path.splitext(name)[1].lower() == '.dll']
    else:
        skip = []

    deps = set()
    for binary in binaries:
        
        name = binary.name

        if name.lower() in skip:
            continue

        name1 = binary.name
        name2 = os.path.basename(binary.path)

        names[name1] = name
        names[name2] = name

        #print("names", name1, name2, names.names(name))

        for dependancy in binary.dependencies:
            if dependancy.lower() in skip:
                continue
            deps.add((binary.name.lower(), dependancy.lower()))
            names[dependancy] = dependancy
    
    digraph = "digraph G {\nnode [shape=rect]\n" + "\n".join(['    "{}" -> "{}"'.format(names[name], names[dependancy]) for name, dependancy in deps]) + "\n}\n"

    if show_graph:
        url = 'https://dreampuf.github.io/GraphvizOnline/#' + urllib.parse.quote(digraph)
        os.startfile(url)

    with open(output, 'w', encoding='utf-8') as f:
        f.write(digraph)

def main():

    colorama_init()

    parser = argparse.ArgumentParser(prog='mugideploy')

    parser.add_argument('command', choices=['update', 'find', 'graph', 'collect', 'inno-script', 'inno-compile', 'build', 'bump-major', 'bump-minor', 'bump-fix', 'show-plugins'])
    
    parser.add_argument('--bin', nargs='+')
    parser.add_argument('--app')
    parser.add_argument('--version')
    parser.add_argument('--data', nargs='+')
    parser.add_argument('--plugins', nargs='+')
    parser.add_argument('--plugins-path', nargs='+')
    parser.add_argument('--toolchain', help="One of: mingw32, vs, cmake (build command)")
    parser.add_argument('--changelog')
    parser.add_argument('--skip', nargs='+', help="Names to skip on collect")
    parser.add_argument('--dest', help="destination path or path template", default='%app%-%version%-%arch%')

    parser.add_argument('--inno-compiler', help='Path to Inno Setup Compiler compil32.exe (including name)')
    parser.add_argument('--vcredist32', help='Path to Microsoft Visual C++ Redistributable x86')
    parser.add_argument('--vcredist64', help='Path to Microsoft Visual C++ Redistributable x64')
    parser.add_argument('--no-vcredist', action='store_true', help='Do not include Visual C++ Redistributable')
    parser.add_argument('--msys-root', help='Msys root')
    parser.add_argument('--msystem', choices=MSYSTEMS, help='msystem')
    parser.add_argument('--unix-dirs', action='store_true', help='bin var etc dirs')

    parser.add_argument('--version-header', help='Path to version.h (including name)')
    parser.add_argument('--src', help='Path to sources')

    parser.add_argument('--save', action='store_true')
    parser.add_argument('--dry-run', action='store_true', help="Do not copy files (collect command)")
    parser.add_argument('--zip', action='store_true', help='Zip collected data')

    # find, graph
    parser.add_argument('-o','--output', help='Path to save dependency tree or graph')
    # graph
    parser.add_argument('--skip-system', action='store_true', help='Skip system32 libraries')
    parser.add_argument('--show', action='store_true', help='Show graph in browser')

    args = parser.parse_args()

    debug_print(args)

    logger = Logger()

    config = read_config()
    update_config(config, args)

    if args.save or args.command == 'update':

        write_config(config)
        if args.version is not None:
            run_version_script(config, logger)
        
        if args.changelog is not None:
            update_changelog(config, config['version'], args.changelog)
        
    if args.command == 'update':
        pass

    elif args.command == 'find':

        if args.output is None:
            print("Specify ouput path")
            exit(1)

        binaries, meta = resolve_binaries(logger, config)
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(binaries, f, ensure_ascii=False, indent=1, cls=JSONEncoder)

    elif args.command == 'graph':

        if args.output is None:
            print("Specify ouput path")
            exit(1)

        binaries, meta = resolve_binaries(logger, config)
        write_graph(binaries, meta, args.output, args.skip_system, args.show)

    elif args.command == 'collect':

        binaries, meta = resolve_binaries(logger, config)
        path = collect(config, logger, binaries, meta, args.dry_run, args.dest, args.skip)
        if args.zip:
            zip_dir(config, logger, path)

    elif args.command == 'inno-script':

        binaries, meta = resolve_binaries(logger, config)
        inno_script(config, logger, binaries, meta)
        
    elif args.command == 'inno-compile':

        inno_compile(config, logger)

    elif args.command in ['bump-major', 'bump-minor', 'bump-fix']:

        bump_version(config, args, logger)

    elif args.command == 'build':

        build(logger, config)


if __name__ == "__main__":
    main()


