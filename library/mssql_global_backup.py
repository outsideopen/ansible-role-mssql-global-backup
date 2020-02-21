#!/usr/bin/python3

# Copyright (c) 2020 David Lundgren

ANSIBLE_METADATA = {
    'metadata_version': '1.1',
    'supported_by': 'community',
    'status': ['preview']
}

DOCUMENTATION = '''
---
module: mssql_global_backup
short_description: Manages a global backup job
description:
    - This will create a SQL Agent job that backs up all databases on the server
version_added: "2.2"
author: David Lundgren (@dlundgren)
options:
    name:
        description:
            - Name of the backup job
        type: str
        required: true
    state:
        description:
            - Whether or not the backup job should be absent or present
            - Use I(enabled) to turn the job on if disabled
            - Use I(disbled) to turn the job off if enabled 
        type: str
        default: "present"
        choices: [ present, absent, enabled, disabled ]
    path:
        description:
            - Path to use instead of C(/var/opt/mssql/backups)
        type: str
        default: "/var/opt/mssql/backups"
    type:
        description:
            - Use I(full) to do a full backup
            - Use I(logs) to do a transaction log backup
        required: false
        type: str
        default: "full"
        choices: [ full, logs ]
    per_database:
        description:
            - Whether or not to store database backups in their own folders
        required: false
        default: true
    rotate:
        description:
            - Number of backups to keep
            - If I(rotate > 0) then the backup file names will appended with the date
        required: false
        default: 0 (not enabled)
    rotate_type:
        description:
            - Use I(day) to keep I(rotate) days worth of backups
            - Use I(count) to keep I(rotate) number of backups
        required: false
        type: str
        default: day
        choices: [ day, count ]
    schedule_type:
        description:
            - Type of schedule to use 
        choices: [ once, daily, weekly, monthly, onstart, idle ]
    schedule_interval:
        description:
            - Interval of the schedule
            - Ignored when I(schedule_type=once)
        required: false
        type: str
        default: "1"
    schedule_start_time:
        description:
            - Start time of the backup job
            - Use 24 hour time
            - Use I(HHMMSS) format, include any leading I(0) to padd it out I(003000) for 12:30 am
        required: false
        default: "000000"
    include:
        description:
            - List of databases to include
        required:false
        type: list  
    exclude:
        description:
            - List of databases to exclude
            - I(master), I(model), I(msdb), I(tempdb) are always excluded, use I(include) to include them
        required: false
        type: list
    login_port:
        description:
            - The TDS port of the instance
        required: false
        default: 1433
    login_name:
        description:
            - The name of the user to log in to the instance
        required: true
    login_password:
        description:
            - The password of the user to log in to the instance
        required: true
notes:
    - Requires the mssql-tools package on the remote host.
requirements:
    - python >= 2.7
    - mssql-tools
'''.replace('\t', '  ')

EXAMPLES = '''
# Backup all user databases
- mssql_global_backup:
    name: all user databases
    login_name: sa
    login_password: password
# backup all databases
- mssql_global_backup:
    name: all databases
    login_name: sa
    login_password: password
    include:
      - master
      - model
      - msdb
      - tempdb
# backup all user databases except northwind
- mssql_global_backup:
    name: all user databases except northwind
    exclude:
      - northwind
    login_name: sa
    login_password: password 
# Custom schedule (using regular time)
- mssql_backup:
    name: all user databases on custom schedule
    schedule_type: daily
    schedule_time: '12:30 am'
    login_name: sa
    login_password: password
'''.replace('\t', '  ')

RETURN = '''
name:
    description: The name of the backup that was managed
    returned: success
    type: string
    sample: foo
state:
    description: The state of the resource (created, updated, disabled, enabled, removed)
    returned: success
    type: string
    sample: created
'''.replace('\t', '  ')

from tempfile import NamedTemporaryFile
from ansible.module_utils.basic import AnsibleModule
import subprocess
import os

def sqlresults(login_port, login_name, login_password, command):
    return subprocess.check_output([
        '/opt/mssql-tools/bin/sqlcmd',
        '-S',
        "localhost,{0}".format(login_port),
        '-U',
        login_name,
        '-P',
        login_password,
        '-d',
        'msdb',
        '-b',
        '-s,',
        '-y0',
        '-Q',
        'SET NOCOUNT ON; %s' % command
    ]).decode()

def sqlfile(login_port, login_name, login_password, command):
    subprocess.check_call([
        '/opt/mssql-tools/bin/sqlcmd',
        '-S',
        "localhost,{0}".format(login_port),
        '-d',
        'msdb',
        '-U',
        login_name,
        '-P',
        login_password,
        '-b',
        '-i',
        command
    ])

def sqlcmd(login_port, login_name, login_password, command):
    subprocess.check_call([
        '/opt/mssql-tools/bin/sqlcmd',
        '-S',
        "localhost,{0}".format(login_port),
        '-d',
        'msdb',
        '-U',
        login_name,
        '-P',
        login_password,
        '-b',
        '-Q',
        command
    ])

def quoteName(name, quote_char):
    if quote_char == '[' or quote_char == ']':
        (quote_start_char, quote_end_char) = ('[', ']')
    elif quote_char == "'":
        (quote_start_char, quote_end_char) = ("N'", "'")
    else:
        raise Exception("Unsupported quote_char {0}, must be [ or ] or '".format(quote_char))

    return "{0}{1}{2}".format(quote_start_char, name.replace(quote_end_char, quote_end_char + quote_end_char), quote_end_char)

class BackupJob:
    def __init__(self, port, user, password, name, include, exclude, per_database, rotate):
        self.port = port
        self.user = user
        self.password = password
        self.name = name
        self.include = include
        self.exclude = exclude
        self.per_database = per_database
        self.rotate = rotate

        self.job_name = name
        self.schedule_name = 'ansible schedule'
        self.backup_step_name = 'ansible backup step'

    def result_filter(self, sql):
        data = [i.strip() for i in sqlresults(self.port, self.user, self.password, sql).split("\n") if i]

        return data

    def job_exists(self):
        sql = "SELECT name FROM dbo.sysjobs WHERE name=N'%s'" % self.job_name
        return self.job_name in sqlresults(self.port, self.user, self.password, sql).split("\n")

    def job_create(self):
        sql = """
            IF NOT EXISTS (
                SELECT name FROM dbo.sysjobs WHERE name={0}
            )
               EXEC sp_add_job @job_name={0}
            ;
        """.format(
            quoteName(self.job_name, "'")
        )
        sqlcmd(self.port, self.user, self.password, sql)

    def backup_step_sql(self, type, path):
        # excludes: name NOT IN (excludes)
        # includes name IN (includes)
        databases = []
        if len(self.include) > 0:
            databases = [ quoteName(name, "'") for name in self.include ]
            where = 'name IN (%s)'
        else:
            databases = [ quoteName(name, "'") for name in self.exclude ]
            where = 'name NOT IN (%s)'

        if len(databases) == 0:
            raise Exception("missing databases: %s" % (','.join(databases)))

        file_path = "'%s'" % path
        if self.per_database:
            file_path = "'%s/' + @name" % path

        file_name = "@name"
        if self.rotate == 0:
            file_name = "@name + '_' + @fileDate"

        # the \r makes it nicely formatted in the database
        return """
DECLARE @name VARCHAR(50);\r
DECLARE @path VARCHAR(256);\r
DECLARE @fileName VARCHAR(256);\r
DECLARE @fileDate VARCHAR(20);\r
SET @fileDate = (Select Replace(Convert(nvarchar, GetDate(), 111), '/', '') + '_' + Replace(Convert(nvarchar, GetDate(), 108), ':', ''));\r
DECLARE db_cursor CURSOR FOR\r
    SELECT name FROM master.sys.databases WHERE {1};\r
OPEN db_cursor;\r
FETCH NEXT FROM db_cursor INTO @name;\r
WHILE @@FETCH_STATUS = 0\r
BEGIN\r
    SET @fileName = {2} + '/' + {3} + '.bak';\r
    BACKUP DATABASE @name TO DISK=@fileName WITH COMPRESSION, NOFORMAT, NOINIT, SKIP, NOREWIND, NOUNLOAD, STATS=10;\r
    FETCH NEXT FROM db_cursor INTO @name;\r
END\r
CLOSE db_cursor;\r
DEALLOCATE db_cursor;\r
GO
        """.format(
            path,
            where % (','.join(databases)),
            file_path,
            file_name
            # ' + '.join(file_name)
        )

    def backup_step_exists(self, type, path):
        sql = """
            SELECT command FROM dbo.sysjobsteps sjs JOIN dbo.sysjobs sj ON (sj.job_id = sjs.job_id)
                WHERE sj.name={0} AND sjs.step_name={1}
        """.format(
            quoteName(self.job_name, "'"),
            quoteName(self.backup_step_name, "'")
        )

        self.step_results = "\n".join(self.result_filter(sql))
        return self.backup_step_sql(type, path) in self.step_results

    def backup_step_manage(self, type, path):
        self.step_manage(self.job_name, self.backup_step_name, 1, self.backup_step_sql(type, path))

        return self.backup_step_exists(type, path)

    def step_manage(self, job_name, step_name, step_id, command):
        sql = """
            IF NOT EXISTS (
                SELECT command FROM dbo.sysjobsteps sjs JOIN dbo.sysjobs sj ON (sj.job_id = sjs.job_id)
                WHERE sj.name={0} AND sjs.step_name={1} 
            )
                BEGIN
                    EXEC sp_add_jobstep 
                        @job_name = {0},
                        @step_name = {1},
                        @subsystem=N'TSQL',
                        @command={2},
                        @retry_attempts=5,
                        @retry_interval=1    
                END
            ELSE
                BEGIN
                    EXEC sp_update_jobstep 
                        @job_name = {0},
                        @step_id = {3}, 
                        @subsystem=N'TSQL',
                        @command={2},
                        @retry_attempts=5,
                        @retry_interval=1
                END
            ;
        """.format(
            quoteName(job_name, "'"),
            quoteName(step_name, "'"),
            quoteName(command, "'"),
            step_id
        )

        path = NamedTemporaryFile()
        path.close()
        with open(path.name, 'w+') as file:
            file.write(sql)

        sqlfile(self.port, self.user, self.password, path.name)
        os.unlink(path.name)


    def schedule_exists(self, type, interval, start_time):
        sql = "SELECT enabled,freq_type,freq_interval,active_start_time FROM dbo.sysschedules WHERE name='%s'" % self.schedule_name
        results = self.result_filter(sql)
        if len(results) > 0:
            if type is '1':
                interval = '0';
            if start_time is '000000':
                start_time = '0'
            else:
                start_time = start_time.lstrip('0')

            self.schedule_results = results

            return ','.join(['1',type,'%d'%interval,start_time]) in results
        return False

    def schedule_manage(self, type, interval, start_time):
        sql = """
            IF NOT EXISTS (
                SELECT * FROM dbo.sysschedules WHERE name={0}
            )
                BEGIN
                    EXEC dbo.sp_add_schedule
                        @schedule_name = {0},
                        @enabled = 1, 
                        @freq_type = {1},
                        @freq_interval = {2},
                        @active_start_time = N'{3}'
                END
            ELSE
                BEGIN
                    EXEC dbo.sp_update_schedule
                        @name = {0},
                        @enabled = 1, 
                        @freq_type = {1},
                        @freq_interval = {2},
                        @active_start_time = N'{3}'                    
                END
            ;
        """.format(
            quoteName(self.schedule_name, "'"),
            type,
            interval,
            start_time
        )
        sqlcmd(self.port, self.user, self.password, sql)

        return self.schedule_exists(type, interval, start_time)


    def schedule_attached(self):
        sql = """
            SELECT sjs.job_id FROM dbo.sysjobschedules sjs 
                LEFT JOIN dbo.sysjobs sj ON (sj.job_id = sjs.job_id)
                LEFT JOIN dbo.sysschedules ss ON (ss.schedule_id = sjs.schedule_id)
                WHERE sj.name={0} and ss.name={1}
        """.format(
            quoteName(self.job_name, "'"),
            quoteName(self.schedule_name, "'")
        )

        self.attach_results = self.result_filter(sql)
        return len(self.attach_results) > 0

    def schedule_attach(self):
        sqlcmd(self.port, self.user, self.password,"""
            EXEC sp_attach_schedule @job_name = {0}, @schedule_name = {1};
            EXEC sp_add_jobserver @job_name = {0}, @server_name=N'(LOCAL)';
        """.format(
            quoteName(self.job_name, "'"),
            quoteName(self.schedule_name, "'")
        ))

        return self.schedule_attached()


def main():
    schedule_types = {
        'once' : '1',
        'daily' : '4',
        'weekly' : '8',
        'monthly' : '16',
        'onstart' : '64',
        'idle' : '128'
    }

    module = AnsibleModule(
        argument_spec = dict(
            name = dict(required = True),
            state = dict(default = 'present', choices=['absent','present','enabled','disabled']),
            path = dict(default = '/var/opt/mssql/backups'),
            type = dict(default = 'full', choices=['full','logs']),

            # rotation options
            rotate = dict(type='int', default = 0),
            rotate_type = dict(default = 'day', choices=['day','count']),
            per_database = dict(type='bool', default = True),

            # primary configuration options
            include = dict(type='list', default = []),
            exclude = dict(type='list', default = []),

            # schedule
            schedule_type       = dict(default = 'daily', choices=schedule_types.keys()),
            schedule_interval   = dict(type='int', default = 1),
            schedule_start_time = dict(default = '000000'),

            # login properties
            login_port     = dict(type='int', required = False, default = 1433),
            login_name     = dict(required = True),
            login_password = dict(required = True, no_log = True)
        ),
        required_if=[
            ['state', 'present', ['path']]
        ]
    )

    backup = BackupJob(
        module.params['login_port'],
        module.params['login_name'],
        module.params['login_password'],
        module.params['name'],
        module.params['include'],
        set(['master', 'model', 'msdb', 'tempdb']) | set(module.params['exclude']),
        module.params['per_database'],
        module.params['rotate']
    )

    changed = False
    state = module.params['state']
    manage = True
    if backup.job_exists():
        if state is 'absent':
            module.fail_json(msg="delete not implemented")
        elif state is 'enabled':
            module.fail_json(msg="enable not implemented")
            # backup.job_enabled()
        elif state is 'disabled':
            module.fail_json(msg="disable not implemented")
            # backup.job_disabled()
    elif state is 'present':
        backup.job_create()
        changed = True

    if manage is True:
        # manage the job step for backup
        type = module.params['type']
        path = module.params['path']
        if not backup.backup_step_exists(type, path):
            if backup.backup_step_manage(type, path):
                changed = True
            # else:
            #     module.fail_json(msg="Unable to update backup step")

        # manage the schedule
        schedule_type = module.params['schedule_type']
        schedule_interval = module.params['schedule_interval']
        schedule_start_time = module.params['schedule_start_time']
        if not backup.schedule_exists(schedule_types[schedule_type], schedule_interval, schedule_start_time):
            if backup.schedule_manage(schedule_types[schedule_type], schedule_interval, schedule_start_time):
                changed = True
            # else:
            #     module.fail_json(msg="Unable to update schedule")

        if not backup.schedule_attached():
            if backup.schedule_attach():
                changed = True
            # else:
            #     module.fail_json(msg="Unable to attach schedule")

    results = {
        'changed': changed,
        'name' : module.params['name'],
        'state': state,
        'results': [
            backup.step_results,
            backup.schedule_results,
            backup.attach_results
        ]
    }

    module.exit_json(**results)


if __name__ == '__main__':
    main()