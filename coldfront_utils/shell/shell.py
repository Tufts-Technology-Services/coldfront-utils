import subprocess


def get_uid(username):
    userline = [i for i in __get_ent('passwd', entry=username).stdout.splitlines() if i.startswith(username + ':')]
    if len(userline) == 0:
        raise ValueError(f"User {username} does not exist")
    return int(userline[0].split(':')[2])


def get_gid(groupname):
    groupline = [i for i in __get_ent('group', entry=groupname).stdout.splitlines() if i.startswith(groupname + ':')]
    if len(groupline) == 0:
        raise ValueError(f"Group {groupname} does not exist")
    return int(groupline[0].split(':')[2])


def __get_ent(catalog, entry=None):
    cmd = ['getent', catalog]
    if entry:
        cmd.append(entry)
    r = subprocess.run(cmd, capture_output=True, encoding='utf-8')
    return r