# mugideploy

mugideploy is C++ deploy utility similar to ldd. It reads info about dynamically linked libraries stored in PE file headers and can:

1) copy all necessary binaries into `name-version-arch` directory (create distribution)

2) copy linked libraries into specified directory

3) create inno setup script to build exe installer

4) print this info in various forms

## Create distribution

```shell
mugideploy collect --version 0.0.1 --bin Release\imgzip.exe --plugins imageformats
```

creates `imgzip-0.0.1-win64` directory and copies `Release\imgzip.exe` and dependencies there

```
options:
  --name NAME                                     App name
  --version VERSION                               App version
  --bin BIN [BIN ...]                             Binaries (dlls, exes)
  --data DATA [DATA ...]                          Path to data dirs and files
  --plugins PLUGINS [PLUGINS ...]                 Plugin names
  --plugins-path PLUGINS_PATH [PLUGINS_PATH ...]  Path to plugins
  --dst DST                                       Destination path or path template
  --vcredist VCREDIST                             Path to Microsoft Visual C++ Redistributable
  --ace ACE                                       Path to Access Database Engine
  --system                                        Include system dlls
  --vcruntime                                     Include vcruntime dlls
  --msapi                                         Include msapi dlls
  -q, --quiet                                     Do not print additional info
  --unix-dirs                                     bin var etc dirs
  --src SRC                                       Path to sources
  --version-header VERSION_HEADER                 Path to version header
  --dry-run                                       Do not copy files
  --zip                                           Zip collected
```

## Copy dependencies

```shell
mugideploy copy-dep --bin C:\qt\6.8.1\mingw_64\bin\qmake.exe --dst C:\qt\6.8.1\mingw_64\bin
```

copies dlls into dst directory

```
options:
  --bin BIN [BIN ...]                             Binaries (dlls, exes)
  --plugins PLUGINS [PLUGINS ...]                 Plugin names
  --plugins-path PLUGINS_PATH [PLUGINS_PATH ...]  Path to plugins
  --dst DST                                       Destination path or path template
  --system                                        Include system dlls
  --vcruntime                                     Include vcruntime dlls
  --msapi                                         Include msapi dlls
  -q, --quiet                                     Do not print additional info
  --dry-run                                       Do not copy files
```

## Create inno setup script

```shell
mugideploy inno-script --bin Release\imgzip.exe --plugins imageformats --data changelog.json -o setup.iss
```

creates `setup.iss`

```
options:
  --bin BIN [BIN ...]                             Binaries (dlls, exes)
  --plugins PLUGINS [PLUGINS ...]                 Plugin names
  --plugins-path PLUGINS_PATH [PLUGINS_PATH ...]  Path to plugins
  --system                                        Include system dlls
  --vcruntime                                     Include vcruntime dlls
  --msapi                                         Include msapi dlls
  -q, --quiet                                     Do not print additional info
  --output-dir OUTPUT_DIR                         Inno setup script output dir
  -o OUTPUT, --output OUTPUT                      Path to save file
```

## Print info

```shell
mugideploy list --bin Release\imgzip.exe --plugins imageformats 2> NUL
```
prints linked libraries as a list

```shell
mugideploy json --bin Release\imgzip.exe --plugins imageformats -o binaries.json
```
prints linked libraries as a json

```shell
mugideploy tree --no-repeat --bin Release\imgzip.exe --plugins imageformats 2> NUL
```
prints linked libraries as a tree

```shell
mugideploy graph --bin Release\imgzip.exe --plugins imageformats -o graph.dot
```
prints linked libraries as a graph

```
options:
  --bin BIN [BIN ...]                             Binaries (dlls, exes)
  --plugins PLUGINS [PLUGINS ...]                 Plugin names
  --plugins-path PLUGINS_PATH [PLUGINS_PATH ...]  Path to plugins
  --system                                        Include system dlls
  --vcruntime                                     Include vcruntime dlls
  --msapi                                         Include msapi dlls
  -q, --quiet                                     Do not print additional info
  -o OUTPUT, --output OUTPUT                      Path to save file
  --no-repeat                                     Print each dll once (tree)
```
