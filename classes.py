from enum import Enum
import json

from copy import copy # already shallowcopy

import os

from datetime import datetime, timedelta, time
from tzlocal import get_localzone

from gcsa.event import Event
from gcsa.google_calendar import GoogleCalendar

# TODO: organise imports

google_account = "" # simply a working email which is organised by oauth
with open("config.json", "r") as f:
    google_account = json.loads(f.read())["email"]

tag = "#auto"
local_timezone = get_localzone()

default_task_length = 30 # for me, when i think of something i might want to do it's research that thing and 30 minutes should be fine

class GCColour(Enum):
    TOMATO = 11
    FLAMINGO = 4
    TANGERINE = 6
    BANANA = 5
    SAGE = 2
    BASIL = 10
    # PEACOCK = None (no colour for the event is this colour)
    BLUEBERRY = 9
    LAVENDER = 1
    GRAPE = 3
    GRAPHITE = 8

def times_intersect(x1,x2,y1,y2):
    if x2 == y1 or y2 == x1:
        return False
    
    return x2 >= y1 and y2 >= x1

def contextualise(time, date):
    """
    Adds a date to a time, allowing for the time to be compared against the global timeline
    """
    return date.replace(hour=time.hour,minute=time.minute,second=time.second)


class Task:
    def __init__(self, name, desc="", minutes=60, due=None):
        # time will be in minutes, just an integer
        self.length = timedelta(minutes=minutes)
        self.due = due # this is an optional parameter, if it doesn't exist then there is no time limit for this task
        self.name = name
        self.desc = desc
    
    def __repr__(self):
        return f"Task({self.name}, {self.desc})"
    
    def obj(self):
        d = {
            "name": self.name,
            "desc": self.desc
        }

        mins = self.length.total_seconds()
        d["length"] = round(mins)

        if self.due is not None:
            d["due"] = self.due.isoformat()
        
        return d
    
    def json(self):
        return json.dumps(self.obj())
    
    @classmethod
    def from_json(self, js):
        d = json.loads(js)
        due = d.get("due")
        if due is not None:
            due = datetime.fromisoformat(due)
        return self(d["name"], d["desc"], d["length"], due)

    @classmethod
    def from_obj(self, d):
        due = d.get("due")
        if due is not None:
            due = datetime.fromisoformat(due)
        return self(d["name"], d["desc"], d["length"], due)
        

    def __eq__(self,other):
        if isinstance(other, Task):
            return self.name == other.name and self.desc == other.desc and self.length == other.length and self.due == other.due

        return False

class Calendar:
    def __init__(self, active_time, inactive_time):
        self.tasks_by_due = [] # every minute all of these are rechecked and uploaded
        self.tasks_pending = [] # in between the minute checks, if tasks are added in between they are placed on pending until the next refresh session

        # the idea of the tasks_pending is so that if the program crashes in between uploads while tasks are trying to be uploaded, they will be saved as not_uploaded
        # every time tasks_pending is added to, a save should be triggered so that next time the program is ran, it will know to refresh

        self.link = GoogleCalendar(google_account) # link to google

        self.log_on = active_time # this should be a datetime object of the time when you start being active
        self.log_off = inactive_time

        self.uploaded_events = [] # needs to be saved, list of Tuple(event_id, event)
        if os.path.isfile("events.json"):
            with open("events.json", "r") as f:
                obj = json.loads(f.read())

                for key,value in obj.items():
                    if key == "not_uploaded":
                        for item in value: # value is here a list[task]
                            task = Task.from_obj(item)
                            self.tasks_pending.append(task)
                            self.tasks_by_due.append(task)
                    else:
                        task = Task.from_obj(value)
                        self.tasks_by_due.append(task)

                        self.uploaded_events.append((key,task))

        self.events = self.get_events()
        self.reload_tasks() # we're back in the business, add tasks that weren't already done, get going
    
    def save_events(self):
        # { event_id: event, ... , not_uploaded: [event, ...]}
        obj = {"not_uploaded": []}

        # merge tasks_by_due and tasks_pending
        tasks = copy(self.tasks_by_due)
        for task in self.tasks_pending:
            if task in tasks:
                continue

            tasks.append(task)
        
        # now check collisions with self.uploaded_events
        uploaded_tasks = [x[1] for x in self.uploaded_events]

        for i, task in enumerate(tasks):
            if task in uploaded_tasks:
                tasks.pop(i)
        
        obj["not_uploaded"] = [x.obj() for x in tasks]

        for i,task in self.uploaded_events:
            obj[i] = task
        
        # now save

        with open("events.json", "w") as f:
            f.write(json.dumps(obj))
    
    def get_events(self):
        """
        DOES NOT MODIFY self.tasks_by_due
        
        Get all upcoming events from Google Calendar which are not tasks managed by this app.
        """
        events = []

        for event in sorted(self.link.get_events()):
            if not (event.description is not None and event.description.endswith(tag)):
                events.append(event)
        
        return events
    
    def get_tasks(self, delete=False):
        """
        DOES NOT MODIFY self.tasks_by_due
        
        Get all upcoming tasks from Google calendar which are managed by this app.
        TODO: deprecate this and use events.json to get events managed by this app.
        """
        tasks = []

        for event in self.link:
            if event.description and event.description.endswith(tag): # all the descriptions must end with this
                tasks.append(event)

                if delete is True:
                    self.link.delete_event(event)
        
        return tasks

    def insert_task(self, task):
        """
        MODIFIES self.tasks_by_due

        Inserts a Task object into the to-do list.
        """
        if v.due is None:
            self.tasks_by_due.append(task)
            return
        
        inserted = False
        for i,v in enumerate(self.tasks_by_due):
            if v.due is None:
                inserted = i
                break

            if v.due > task.due:
                self.tasks_by_due.insert(i,task)
                inserted = True
                break
        
        if inserted is not True:
            self.tasks_by_due.insert(inserted,task) # the index in which the tasks start to have no due date
    
    def merge_pending(self):
        for _ in self.tasks_pending:
            self.insert_task(self.tasks_pending.pop())

    def upload_task_list(self,task_list):
        """
        Takes a List[Tuple[datetime, Task]] and uploads all tasks as events onto Google Calendar.
        """
        for time,task in task_list:
            event = Event(start=time,end=time+task.length,description=task.desc+tag,color_id=GCColour.TOMATO.value,summary=task.name)

            event = self.link.add_event(event)

            self.uploaded_events.append((event.event_id, task))
        
        self.save_events()
    
    def check_event_updates(self):
        """
        Goes through all of the tasks uploaded, and checks for modifications. If modified, update the clientside tasks to the ones prompted by the user.        
        """
        for task, event in self.get_uploaded_tasks():
            if event.start is not None and event.end is not None:
                length = round((event.end - event.start).total_seconds / 60)

                if length != round(task.length.total_seconds / 60):
                    task.length = timedelta(minutes=length)
            
            if event.summary != task.name:
                task.name = event.summary
            
            if event.description != task.desc + tag:
                task.desc = event.description[:len(tag)]
            
            if event.color_id in [GCColour.BASIL.value, GCColour.SAGE.value]:
                for i,t in enumerate(self.tasks_by_due):
                    if task == t:
                        self.tasks_by_due.pop(i)

                        self.save_events()

    def get_uploaded_tasks(self, filterCompleted=False):
        """
        Gets all uploaded tasks as Tuple(task, event) from Google Calendar as GCSA Events and returns them as a list.
        """
        tasks = []

        for event_id, task in self.uploaded_events:
            event = self.link.get_event(event_id)
            if filterCompleted and task.color_id in [GCColour.BASIL.value, GCColour.SAGE.value]:
                # it's completed (marked as green), skip it
                continue
            
            tasks.append((task,event))

        return tasks
    
    def organise_calendar(self):
        """
        DOES NOT MODIFY self.tasks_by_due

        Creates a valid new calendar, organising tasks by due date and around events.
        
        To be implemented in order of priority:
        1) (Assuming all tasks are assignable easily in the order by due date) lay out all tasks by due date. ✅
        2) Lay out all tasks by due date if tasks can be switched around and still give a valid layout. (ex: short task taking up space on day 2 when day 1 has a gap free for it)
            - do 2 layers of recursion, in which we heuristically swap two random events, check the layout's validity and keep going#
            - if this fails, move onto step 3
        3) Split up tasks into smaller segments (if needed and opted in)
        4) If the layout isn't possible and you're swamped, extend log_off time by increments of 30 minutes (includes log_off time being technically before log_on time, if log_off is 2am and log_on is 7am)
        """
        
        self.merge_pending() # get up to speed on all tasks

        # let's find the first active moment that's a multiple of 15 minutes after when this is being run

        now = datetime.now(local_timezone) 
        fifteen_minutes = timedelta(minutes=15)

        current_minute = now.minute
        if current_minute % 15 != 0:
            # i couldn't think of a better way to do this
            for i in range(15):
                current_minute -= 1
                if current_minute % 15 == 0:
                    break
        
        now = now.replace(minute=current_minute,second=0,microsecond=0)
        now += fifteen_minutes # this is now the first 15 minute starter

        now_time = now.time()

        if (now_time < self.log_on) or (now_time > self.log_off): # this assumes that if placed on the same day, log_on would be before log_off
            # we're in inactive hours, skip ahead to you logging on

            while (now_time < self.log_on) or (now_time > self.log_off):
                # continue adding 15 minutes until it is a valid time
                now += fifteen_minutes
                now_time = now.time()
        
        # now_time is now a valid time to try the place to calendar logic
        
        working_time = contextualise(now_time, now)

        task_list = [] # tuple(time: starting_time, task: task assigned to this time)

        # keep skipping forwards fifteen minutes until a slot is found which doesn't collide with log_off time and any scheduled events

        tasks_by_due = copy(self.tasks_by_due)

        for i in range(len(tasks_by_due)):
            task = tasks_by_due.pop(0)

            while True: # logic to break is too complicated to be handled in a statement
                valid = True

                end = working_time + task.length

                # consider a number line and the intersection of 2 1d segments
                # additionally, if the log_on or log_off lies within working_time and end, it is also invalid (this logic is harder though)

                # if the starting time of the event we are on is after end, then we can break the loop (iterating through events is hard when it goes through the next year's worth)

                for event in self.events:
                    print(event, event.start, event.end, working_time, "event loop")
                    # check for collisions as described above
                    if times_intersect(working_time, end, event.start, event.end):
                        valid = False
                        break # we don't need to check any more

                    if event.start > end:
                        # all further events are after this
                        break
                
                # log_on log_off collision cases:
                # log_on start log_off end
                # log_on start log_off log_on .... end

                # place the log_off time on the date at which the task will be done
                day_specific_log_off = contextualise(self.log_off, working_time)

                if end > day_specific_log_off:
                    # this is an impossible configuration, it isn't valid
                    valid = False
                
                # currently the assumption is that all tasks are able to be fit in order before the due date, so task.due isn't referenced yet
                # TODO: task.due

                if valid is True:
                    break

                working_time += fifteen_minutes

            task_list.append((working_time, task))
            working_time += task.length
        
        return task_list

    def reload_tasks(self, tasks): # TODO:
        """
        Reorganises the calendar according to the current event layout.
        """
        
        # if the time is in the middle of a task, organise_calendar will shift this task along infinitely and we don't want that to happen
        # redesign organise_calendar to fit this description


                
# # cheeky little test case

# c = Calendar(time(hour=7), time(hour=18))

# t1 = Task("not important", "do something", 60,0)
# t2 = Task("pop method", "pop 1 singular method", 30,0)
# t3 = Task("poop", "take a poo", 120,0)

# c.insert_task(t1)
# c.insert_task(t2)
# c.insert_task(t3)

# tl = c.organise_calendar()

# c.upload_task_list(tl)