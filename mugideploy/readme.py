import subprocess

lines = subprocess.check_output(['python','-m','mugideploy','--help']).decode('utf-8').split('\n')[10:]

def without(lines, filters):
    res = []
    for line in lines:
        inc = True
        for f in filters:
            if f in line:
                inc = False
        if inc:
            res.append(line)
    return res

only = {
    'none': ['--help'],
    'tree': ['--no-repeat'],
    'inno-script': ['--output-dir'],
    'collect': ['--vcredist', '--ace', '--unix-dirs', '--data', '--src', '--version', '--name', '--zip'],
}

commands = [
    'collect', 'copy-dep', 'inno-script','tree',
]

with open('out.txt', 'w', encoding='utf-8') as f:
    for command in commands:
        filters = []
        for cmd, fs in only.items():
            if cmd != command:
                filters.extend(fs)

        if command not in ['collect', 'copy-dep']:
            filters.extend(['--dst', '--dry-run'])
        if command in ['copy-dep', 'collect']:
            filters.append('--output')

        text = "".join(without(lines, filters))
        print(file = f)
        print(command, file=f)
        print(text, file=f)