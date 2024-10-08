import polars as pl
import win32com.client
from typing import Literal
from datetime import datetime, timedelta
from pytask_scheduler import (
    TaskActionTypes,
    TaskTriggerTypes,
    TaskCreationTypes,
    TaskLogonTypes,
    TaskValueDefinitions
)

class TasksDataFrame(pl.DataFrame):
    """Data frame for scheduled tasks."""
    def __init__(self, data: pl.DataFrame):
        super().__init__(data)
        self.df = data

    def preprocess(self):
        """Preprocess the tasks data frame."""
        df = (self.df
            .with_columns(
                pl.col("task_state")
                .cast(str)
                .replace(TaskValueDefinitions.TASK_STATE_DEFINITION)
                .alias("task_state_definition"),
                pl.col("last_task_result")
                .cast(str)
                .replace(TaskValueDefinitions.TASK_RESULT_DEFINITION)
                .alias("last_task_result_definition"),
                pl.col("next_run_time").cast(pl.Datetime),
                pl.col("last_run_time").cast(pl.Datetime),
                pl.col("task_path").str.split("\\").alias("task_folder_name")
            )
            .with_columns(
                pl.when(pl.col("task_folder_name").list.len() <= 2)
                .then(pl.lit("\\"))
                .otherwise(pl.col("task_folder_name").list.slice(1,2).list.first())
                .name.keep()
            )
            .select(
                [
                    'task_source',
                    'task_path',
                    'task_folder_name',
                    'name',
                    'task_description',
                    'enabled',
                    'task_state',
                    'task_state_definition',
                    'next_run_time',
                    'last_run_time',
                    'last_task_result',
                    'last_task_result_definition',
                    'number_of_missed_runs',
                    'author',
                    'registration_date',
                    'execution_path',
                    'AllowDemandStart',
                    'StartWhenAvailable',
                    'Enabled',
                    'Hidden',
                    'RestartInterval',
                    'RestartCount',
                    'ExecutionTimeLimit',
                    'MultipleInstances'
                ]
            )
        )
        return TasksDataFrame(df)

    def stats(self) -> pl.DataFrame:
        """Get statistics on all the tasks."""
        stats_schema = {
            "task_total":self.total_number_of_tasks(),
            "missed_runs_total":self.total_number_of_missed_runs(),
            "unknown_state_total":self.total_number_of_tasks_by_state(0),
            "disabled_state_total":self.total_number_of_tasks_by_state(1),
            "queued_state_total":self.total_number_of_tasks_by_state(2),
            "ready_state_total":self.total_number_of_tasks_by_state(3),
            "running_state_total":self.total_number_of_tasks_by_state(4)
        }

        return pl.DataFrame(stats_schema)

    def total_number_of_tasks(self):
        """Total number of scheduled tasks, this will include disabled tasks."""
        return self.df.shape[0]

    def total_number_of_missed_runs(self):
        """Total number of missed runs."""
        return self.df["number_of_missed_runs"].sum()

    def total_number_of_tasks_by_state(self, task_state: Literal[0,1,2,3,4]):
        """Total number of scheduled tasks filtered by the task state."""
        return self.df.filter(pl.col("task_state")==task_state).shape[0]
    
    def get_tasks_completed_today(self):
        """Get the scheduled tasks that were completed today."""
        todays_date = datetime.today().date()
        current_datetime = datetime.now()
        df = (self.df
            .filter(
                pl.col("last_run_time")
                .cast(pl.Datetime)
                .is_between(todays_date,current_datetime)
            )
            .sort("last_run_time", descending=True)
        )
        return TasksDataFrame(df)
    
    def get_tasks_due_today(self):
        """Get the tasks that are due to execute today."""
        MAX_HOUR = 23
        MAX_MIN = 59
        MAX_SEC = 59
        MAX_MSEC = 999999

        current_dt = datetime.now()

        lower_date = current_dt

        thour = lower_date.hour
        tmin = lower_date.minute
        tsec = lower_date.second
        tmsec = lower_date.microsecond

        upper_date = (current_dt.date() +
            timedelta(
                hours=MAX_HOUR-thour,
                minutes=MAX_MIN-tmin,
                seconds=MAX_SEC-tsec,
                microseconds=MAX_MSEC-tmsec
            )
        )

        df = (self.df
            .filter(
                pl.col("next_run_time")
                .cast(pl.Datetime)
                .is_between(
                    lower_date,
                    upper_date
                )
            )
            .sort("next_run_time")
        )

        return TasksDataFrame(df)

class HistoryDataFrame(pl.DataFrame):
    """Data frame for the task history."""
    def __init__(self, data: pl.DataFrame):
        super().__init__(data)
        self.df = data

    def preprocess(self):
        """Preprocessing for the historical data frame."""
        df = (self.df
            .rename({
                "event_created_time":"Event Created",
                "event_level":"Event Level",
                "event_id":"Event ID",
                "task_name":"Task Name",
                "event_id_description":"Event ID Description",
                "event_log_description":"Event Log Description"
            }) 
            .with_columns(
                pl.col("Event Created")
                .str.strptime(pl.Datetime,"%Y-%m-%d %H:%M:%S%.6f")
                .name.keep(),
                pl.col("Event Level").cast(pl.Int64),
                pl.col("Event ID").cast(pl.Int64),
                pl.col("Task Name").str.split("\\").list.last().name.keep()
            )
        )
        return HistoryDataFrame(df)

    def get_todays_history(self):
        """Filter the history data frame based on today's date."""
        df = self.df.filter(
            pl.col("Event Created").cast(pl.Date)==datetime.now().date()
        ).sort("Event Created", descending=True)
        return HistoryDataFrame(df)

    def __event_count_by_criteria(self, filter_col: str, filter_criteria: str) -> int:
        """Counts the number of events based on the filter column and criteria."""
        return self.df.filter(pl.col(filter_col)==filter_criteria).shape[0]

    def information_event_count(self) -> int:
        """Returns the count of information events."""
        return self.__event_count_by_criteria("Event Log Description","INFORMATION")

    def error_event_count(self) -> int:
        """Returns the count of error events."""
        return self.__event_count_by_criteria("Event Log Description","ERROR")

    def warning_event_count(self) -> int:
        """Returns the count of warning events."""
        return self.__event_count_by_criteria("Event Log Description","WARNING")

class TaskScheduler:
    """
    Task Scheduler object.
    
    Attributes:
        client (`CDispath`): The schedule.service com object from win32com.
        root_folder: Root folder object.
        folders (`list`): List of the subfolder names from the root folder.
    """
    def __init__(self):
        self.client = win32com.client.gencache.EnsureDispatch("Schedule.Service")
        self.client.Connect()
        self.root_folder = self.client.GetFolder("\\")
        self.folders = [f.Name for f in self.root_folder.GetFolders(0)]

    def __find_folder(self, folder, folder_name: str) -> str:
        """Find and return the folder path from task scheduler."""
        folders = folder.GetFolders(0)
        for subfolder in folders:
            if subfolder.Name == folder_name:
                return subfolder.Path
            fpath = self.__find_folder(subfolder, folder_name)
            if fpath:
                return fpath
        return None

    def get_folder(self, folder_name: str|None=None):
        """Get the folder object.
        
        Parameters:
            folder_name (`str`): Folder name to look for.

        Returns:
            TaskFolder object.
        """

        if folder_name is None:
            return TaskFolder(self.root_folder)
        else:
            folder_path = self.__find_folder(self.root_folder, folder_name)
            if folder_path:
                folder = self.client.GetFolder(folder_path)
                return TaskFolder(folder)
            else:
                raise ValueError(f"Could not find {folder_name}")

    def __list_tasks_info_in_folder(self, folder, folder_name: str, tasks_info_list: list):
        """Recursively list all tasks within folders."""

        # extract and append all the tasks info in the list.
        tasks = folder.tasks
        for t in tasks:
            tasks_info_list.append(folder.get_task(t).info())

        # walk through all subfolders and get the tasks info.
        subfolders = folder.subfolders
        for sf in subfolders:
            subfolder = self.get_folder(sf)
            self.__list_tasks_info_in_folder(
                subfolder,
                folder_name + "\\" + sf,
                tasks_info_list
            )

    def get_all_tasks(self) -> pl.DataFrame:
        """Method for extracting all the scheduled tasks."""
        root_folder = self.get_folder()
        tasks_info_list = []
        self.__list_tasks_info_in_folder(root_folder, "\\", tasks_info_list)
        df = pl.DataFrame(tasks_info_list)
        df = TasksDataFrame(df).preprocess()
        return df

    def create_task(
        self,
        folder_name: str,
        trigger_type: Literal["daily","weekly","monthly","monthlydow","one-time"],
        start_date: datetime.date,
        start_time: datetime.time,
        days_interval: int|None,
        weeks_interval: int|None,
        days_of_week: int|None,
        days_of_month: int|None,
        months_of_year: int|None,
        weeks_of_month: int|None,
        action_type: Literal["exec","com-handler","email","show-message"],
        action_arg: str|None,
        action_file: str,
        action_working_dir: str|None,
        task_name: str,
        task_description: str,
        allow_demand_start: bool|None,
        start_when_available: bool|None,
        enabled: bool|None,
        hidden: bool|None,
        restart_interval: str|None,
        restart_count: int|None,
        execution_time_limit: str|None,
        multiple_instances: int|None

    ):
        """Create a new task.

        Parameters:
            folder_name (`str`): Folder name in which the task will be registered to.
            trigger_type (`str`): `Literal["daily","weekly","monthly","monthlydow","one-time"]` \
                Indicates the type of trigger to create.
            start_date (`datetime.date`): Date when the trigger is activated.
            start_time (`datetime.time`): Time when the trigger is activated.
            days_interval (`int`): Required for a daily trigger. Sets the interval between the \
                days in the schedule. An interval of 1 produces a daily schedule, an interval \
                of 2 produces an every-other day schedule.
            weeks_interval (`int`): Required for a weekly trigger. Sets the interval between \
                the weeks in the schedule. An interval of 1 produces a weekly schedule, an \
                interval of 2 produces an every-other week schedule.
            days_of_week (`int`):
            days_of_month (`int`):
            months_of_year (`int`):
            weeks_of_month (`int`):
            action_type (`str`): Literal["exec","com-handler","email","show-message"],
            action_arg (`str`):
            action_file (`str`):
            action_working_dir (`str`):
            task_name (`str`):
            task_description (`str`):
            allow_demand_start (`bool`):
            start_when_available (`bool`):
            enabled (`bool`):
            hidden (`bool`):
            restart_interval (`str`):
            restart_count (`int`):
            execution_time_limit (`str`):
            multiple_instances (`int`):

        """
        folder = self.get_folder(folder_name)

        # create task def.
        new_taskdef = self.client.NewTask(0)

        # create new trigger.
        match trigger_type:
            case "daily":
                new_taskdef = TaskTrigger(new_taskdef).create_daily_trigger(
                    start_date=start_date,
                    start_time=start_time,
                    days_interval=days_interval
                )

            case "weekly":
                new_taskdef = TaskTrigger(new_taskdef).create_weekly_trigger(
                    start_date=start_date,
                    start_time=start_time,
                    weeks_interval=weeks_interval,
                    days_of_week=days_of_week
                )

            case "monthly":
                new_taskdef = TaskTrigger(new_taskdef).create_monthly_trigger(
                    trigger_type="month",
                    start_date=start_date,
                    start_time=start_time,
                    days_of_month=days_of_month,
                    days_of_week=days_of_week,
                    months_of_year=months_of_year,
                    weeks_of_month=weeks_of_month
                )

            case "monthlydow":
                new_taskdef = TaskTrigger(new_taskdef).create_monthly_trigger(
                    trigger_type="dow",
                    start_date=start_date,
                    start_time=start_time,
                    days_of_month=days_of_month,
                    days_of_week=days_of_week,
                    months_of_year=months_of_year,
                    weeks_of_month=weeks_of_month
                )

            case "one-time":
                new_taskdef = TaskTrigger(new_taskdef).create_one_time_trigger(
                    start_date=start_date,
                    start_time=start_time
                )

        # create a new task action
        match action_type:
            case "exec":
                new_action = TaskAction(new_taskdef).create_execution_action(
                    argument=action_arg,
                    filepath=action_file,
                    working_dir=action_working_dir
                )
            case "com-handler":
                raise NotImplementedError("Create com handler action has not been implemented")
            case "email":
                raise NotImplementedError("Create send email action has not been implemented")
            case "show-message":
                raise NotImplementedError("Create show message action has not been implemented")

        # add task description
        new_taskdef.RegistrationInfo.Description = task_description

        # task settings
        new_taskdef.Settings.AllowDemandStart = allow_demand_start
        new_taskdef.Settings.StartWhenAvailable = start_when_available
        new_taskdef.Settings.Enabled = enabled
        new_taskdef.Settings.Hidden = hidden
        new_taskdef.Settings.RestartInterval = restart_interval
        new_taskdef.Settings.RestartCount = restart_count
        new_taskdef.Settings.ExecutionTimeLimit = execution_time_limit
        new_taskdef.Settings.MultipleInstances = multiple_instances

        folder.register_new_task(task_name,new_taskdef)
        return NewTask(new_taskdef)

class NewTask:
    """This object covers topics from the TaskDefinition scripting object.
    https://learn.microsoft.com/en-us/windows/win32/taskschd/taskdefinition
    """
    def __init__(self, taskdef_obj):
        self.taskdef = taskdef_obj

class RegisteredTask:
    """This object covers some of the api from the RegisteredTask scripting object.
        https://learn.microsoft.com/en-us/windows/win32/taskschd/registeredtask
    """
    def __init__(self, rtask_obj):
        self.rtask = rtask_obj
        self.xml = rtask_obj.Xml
        self.taskdef = self.rtask.Definition
        self.reg_info = self.taskdef.RegistrationInfo
        self.task_settings = self.taskdef.Settings

    def info(self) -> dict:
        """Information on registered task."""
        return {
            "name":self.rtask.Name,
            "enabled":self.rtask.Enabled,
            "task_state":self.rtask.State,
            "next_run_time":self.rtask.NextRunTime,
            "last_run_time":self.rtask.LastRunTime,
            "last_task_result":self.rtask.LastTaskResult,
            "number_of_missed_runs":self.rtask.NumberOfMissedRuns,
            "task_path":self.rtask.Path,
            "author":self.reg_info.Author,
            "registration_date":self.reg_info.Date,
            "task_description":self.reg_info.Description,
            "task_source":self.reg_info.Source,
            "AllowDemandStart": self.task_settings.AllowDemandStart,
            "StartWhenAvailable": self.task_settings.StartWhenAvailable,
            "Enabled": self.task_settings.Enabled,
            "Hidden": self.task_settings.Hidden,
            "RestartInterval": self.task_settings.RestartInterval,
            "RestartCount": self.task_settings.RestartCount,
            "ExecutionTimeLimit": self.task_settings.ExecutionTimeLimit,
            "MultipleInstances": self.task_settings.MultipleInstances,
            "execution_path":self.__extract_action_execpath()
        }

    def __extract_action_execpath(self):
        """Gets the action file path from the tasks xml text."""
        exepath = ""
        import xml.etree.ElementTree as ET
        root = ET.fromstring(self.xml)
        for act in root.findall('{http://schemas.microsoft.com/windows/2004/02/mit/task}Actions'):
            for exe in act.findall('{http://schemas.microsoft.com/windows/2004/02/mit/task}Exec'):
                for command in exe.findall('{http://schemas.microsoft.com/windows/2004/02/mit/task}Command'):
                    exepath = command.text

        return exepath

    def update_registration_info(self, task_description: str):
        """Updates the registration info for a task.

        Parameter:
            task_description (`str`): Description of a task.
        
        Support:
            Only supports updating the task description.

        Returns:
            TaskDefinition object.
        """
        self.reg_info.Description = task_description
        return self.taskdef

class TaskAction:
    """This object covers topics from the Action scripting object.
    https://learn.microsoft.com/en-us/windows/win32/taskschd/action
    """
    def __init__(self, taskdef_obj):
        self.action = taskdef_obj.Actions

    def __set_action_type(self, action_type: int):
        self.action.Create(action_type)

    def create_execution_action(
        self,
        filepath: str,
        argument: str|None="",
        working_dir: str|None=""
    ):
        """Creates an action that executes a command-line operation. For example, \
            can execute a batch file script.
        https://learn.microsoft.com/en-us/windows/win32/taskschd/execaction

        Parameters:
            argument (`str`): Sets the arguments associated with the command-line operation.
            filepath (`str`): Sets the path to an executable file.
            working_dir (`str`): Sets the directory that contains either the executable file \
            or the files that are used by the executable file.
        """
        self.__set_action_type(TaskActionTypes.TASK_ACTION_EXEC)
        self.action.Path = filepath
        self.action.Arguments = argument
        self.action.WorkingDirectory = working_dir
        return self.action

    def create_com_handler_action(self):
        """Creates an action that fires a handler.
        https://learn.microsoft.com/en-us/windows/win32/taskschd/comhandleraction
        """
        raise NotImplementedError("Create com handle action has not been implemented")

    def create_send_email_action(self):
        """Creates an action that send an email message.
        https://learn.microsoft.com/en-us/windows/win32/taskschd/emailaction
        """
        raise NotImplementedError("Create send email action has not been implemented")

    def create_show_message_action(self):
        """Creates an action that shows a message box when a task is activated.
        https://learn.microsoft.com/en-us/windows/win32/taskschd/showmessageaction
        """
        raise NotImplementedError("Create show message action has not been implemented")

class TaskFolder:
    """This object covers the TaskFolder scripting object. 
        https://learn.microsoft.com/en-us/windows/win32/taskschd/taskfolder
    """

    def __init__(self, folder_obj):
        self.folder = folder_obj
        self.subfolders = [f.Name for f in self.folder.GetFolders(0)]
        self.tasks = [t.Name for t in self.folder.GetTasks(0)]

    def info(self) -> dict:
        """Folder information stored in a hash table.
        
        Returns:
            Dictionary containing folder information.
        """
        return {
            "subfolders":self.subfolders,
            "tasks":self.tasks,
            "folder_name":self.folder.Name,
            "folder_path":self.folder.Path,
        }

    def create_folder(self, folder_name: str):
        """Creates a new subfolder.
        
        Parameters:
            folder_name (`str`): Name for the subfolder.

        Returns:
            TaskFolder object.
        """
        if folder_name in self.subfolders:
            raise ValueError(f"{folder_name} already exists!")
        else:
            new_folder = self.folder.CreateFolder(folder_name)
            return TaskFolder(new_folder)

    def delete_folder(self, folder_name: str):
        """Deletes the subfolder.

        Parameters:
            folder_name (`str`): Name for the subfolder.
        """
        if folder_name in self.subfolders:
            self.folder.DeleteFolder(folder_name)
        else:
            raise ValueError(f"{folder_name} does not exist!")

    def get_task(self, task_name: str):
        """Get the task object by name.
        
        Returns:
            RegisteredTask object.
        """
        if task_name in self.tasks:
            rtask = self.folder.GetTask(task_name)
        else:
            raise ValueError(f"{task_name} does not exist in this folder!")

        return RegisteredTask(rtask)
    
    def register_new_task(
        self,
        task_name: str,
        new_taskdef
    ):
        self.folder.RegisterTaskDefinition(
            task_name,
            new_taskdef,
            TaskCreationTypes.TASK_CREATE_OR_UPDATE,
            "", # no username
            "", # no password
            TaskLogonTypes.TASK_LOGON_NONE
        )
 
class TaskSettings:
    """This object covers topics from the TaskSettings object.
        https://learn.microsoft.com/en-us/windows/win32/taskschd/tasksettings
    """

    def __init__(self, taskdef_obj):
        self.taskdef = taskdef_obj
        self.task_settings = self.taskdef.Settings

    def update_settings(
        self,
        allow_demand_start: bool,
        start_when_available: bool,
        enabled: bool,
        hidden: bool,
        restart_interval: str,
        restart_count: int,
        execution_time_limit: str,
        multiple_instances: int
    ):
        """
        Updates the settings of a task.
        
        Parameters:
            allow_demand_start (`bool`):
                Gets or sets a boolean value that indicates that the task can be started by using \
                either the run command of the context menu.

            start_when_available (`bool`):
                Gets or sets a boolean value that indicates that the task scheduler can start the \
                task at any time after its scheduled time has passed.

            enabled (`bool`):
                Gets or sets a boolean value that indicates that the task is enabled. The task can \
                be performed only when this setting is True.

            hidden (`bool`):
                Gets or sets a boolean value that indicates that the task will not be visible in \
                the UI. However, admins can override this setting through the use of a \
                'master switch' that makes all tasks visible in the UI.

            restart_interval (`str`):
                Gets or sets a value that specifies how long the task scheduler will \
                attempt to restart the task.

            restart_count (`int`): 
                Gets or sets the number of times that the task scheduler \
                will attempt to restart the task.

            execution_time_limit (`str`): 
                Gets or sets the amount of time allowed to complete the task.

            multiple_instances (`int`): 
                Gets or sets the policy that defines how the task scheduler deals with \
                multiple instances of the task. 
        
        """
        self.task_settings.AllowDemandStart = allow_demand_start
        self.task_settings.StartWhenAvailable = start_when_available
        self.task_settings.Enabled = enabled
        self.task_settings.Hidden = hidden
        self.task_settings.RestartInterval = restart_interval
        self.task_settings.RestartCount = restart_count
        self.task_settings.ExecutionTimeLimit = execution_time_limit
        self.task_settings.MultipleInstances = multiple_instances

        return self.taskdef
    
class TaskTrigger:
    """This module covers topics from the Trigger scripting objects.
        https://learn.microsoft.com/en-us/windows/win32/taskschd/trigger
    """
    def __init__(self, taskdef_obj):
        self.taskdef = taskdef_obj
        self.trigger = self.taskdef.Triggers

    def __set_start_boundary(self, start_date: datetime.date, start_time: datetime.time):
        self.trigger.StartBoundary = datetime.combine(start_date, start_time).isoformat()

    def __set_cadence(self, trigger_type: int):
        self.trigger.Create(trigger_type)

    def create_daily_trigger(
        self,
        start_date: datetime.date,
        start_time: datetime.time,
        days_interval: int
    ):
        """Starts a task based on a daily schedule. 
        https://learn.microsoft.com/en-us/windows/win32/taskschd/dailytrigger
        
        Parameters:
            start_date (`datetime.date`): Date when the trigger is activated.
            start_time (`datetime.time`): Time when the trigger is activated.
            days_interval (`int`): Sets the interval between the days in the schedule. \
                An interval of 1 produces a daily schedule, an interval of 2 produces an \
                every-other day schedule.
        """
        self.__set_cadence(TaskTriggerTypes.TASK_TRIGGER_DAILY)
        self.__set_start_boundary(start_date, start_time)
        self.trigger.DaysInterval = days_interval
        return self.taskdef

    def create_weekly_trigger(
        self,
        start_date: datetime.date,
        start_time: datetime.time,
        weeks_interval: int,
        days_of_week: list[int]
    ):
        """Starts a task based on a weekly schedule. For example, the task starts at 8:00 AM \
            on a specific day of the week every week or every other week.
            https://learn.microsoft.com/en-us/windows/win32/taskschd/weeklytrigger

        Parameters:
            start_date (`datetime.date`): Date when the trigger is activated.
            start_time (`datetime.time`): Time when the trigger is activated.
            weeks_interval (`int`): Sets the interval between the weeks in the schedule. \
                An interval of 1 produces a weekly schedule, an interval of 2 produces an \
                every-other week schedule.
            days_of_week: Sets the days on which the task will run.
        """
        self.__set_cadence(TaskTriggerTypes.TASK_TRIGGER_WEEKLY)
        self.__set_start_boundary(start_date, start_time)
        self.trigger.WeeksInterval = weeks_interval
        self.trigger.DaysOfWeek = days_of_week
        return self.taskdef

    def create_monthly_trigger(
        self,
        trigger_type: Literal["month","dow"],
        start_date: datetime.date,
        start_time: datetime.time,
        days_of_month: list[int],
        days_of_week: list[int],
        months_of_year: list[int],
        weeks_of_month: int
    ):
        """Starts a task based on a monthly schedule or a monthly day-of-week schedule.
        https://learn.microsoft.com/en-us/windows/win32/taskschd/monthlytrigger
        
        Parameters:
            trigger_type (`Literal['month', 'dow']`): Type of monthly trigger.
            start_date (`datetime.date`): Date when the trigger is activated.
            start_time (`datetime.time`): Time when the trigger is activated.
            days_of_month (`int`): Sets the days of the month during which the task runs.
            days_of_week (`int`): Sets the days of the week during which the task runs. 
            months_of_year (`int`): Sets the months of the year during which the task runs.
            weeks_of_month (`int`): Sets the weeks of the month during which the task runs.
        
        Examples:
            The `month` trigger type can start a task on a specific day of specific months.
            The `dow` trigger type can start a task every first Thursday of specific months.
        
        """
        self.__set_start_boundary(start_date, start_time)
        match trigger_type:
            case "month":
                self.__set_cadence(TaskTriggerTypes.TASK_TRIGGER_MONTHLY)
                self.trigger.DaysOfMonth = days_of_month
                self.trigger.MonthsOfYear = months_of_year
            case "dow":
                self.__set_cadence(TaskTriggerTypes.TASK_TRIGGER_MONTHLYDOW)
                self.trigger.DaysOfWeek = days_of_week
                self.trigger.MonthsOfYear = months_of_year
                self.trigger.WeeksOfMonth = weeks_of_month
        return self.taskdef

    def create_one_time_trigger(
        self,
        start_date: datetime.date,
        start_time: datetime.time
    ):
        """Starts a task at as specific date and time.
        https://learn.microsoft.com/en-us/windows/win32/taskschd/timetrigger

        Parameters:
            start_date (`datetime.date`): Date when the trigger is activated.
            start_time (`datetime.time`): Time when the trigger is activated.
        """
        self.__set_cadence(TaskTriggerTypes.TASK_TRIGGER_TIME)
        self.__set_start_boundary(start_date, start_time)
        return self.taskdef