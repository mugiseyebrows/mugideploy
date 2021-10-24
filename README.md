# mugideploy

mugideploy is C++ deploy utility. 

Bashwise speaking it can be expressed as (pseudocode):

```bash
mkdir dist
cp $target dist
for dep in `ldd $target`; do 
    cp $dep dist
done
```

# Usage by example

```cmd
mugideploy collect --bin path\to\myapp.exe
```

Creates directory `myapp-0.0.1` and stores `myapp.exe` and all its dependent dlls there (make sure that dependencies directories in %PATH% environment variable). If it's qt app, adds `myapp-0.0.1\qt.conf` and necessary plugins.

```cmd
mugideploy collect --bin path\to\myapp.exe --plugins qsqlmysql
```

Also pulls `qsqlmysql.dll` (and it's dependencies) and stores it in `myapp-0.0.1\plugins`

To specify name and version use `--app` and `--version`

```cmd
mugideploy collect --app app --version 1.0.0 --bin path\to\myapp.exe
```

To store data in `mugideploy.json` and use it later, run `mugideploy init` and `mugideploy update`.

```cmd
mugideploy init --bin path\to\myapp.exe --plugins qsqlmysql
mugideploy update --version 1.1.0 --changelog "fixed random bug"
make
mugideploy collect
```

To create innosetup script and compile it into `setup.exe` distribution run

```cmd
mugideploy inno-script --bin path\to\myapp.exe
mugideploy inno-compile
```

