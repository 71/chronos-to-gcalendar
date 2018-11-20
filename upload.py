import arrow, requests

from datetime                   import date, datetime, time, timedelta
from googleapiclient.discovery  import build
from httplib2                   import Http
from oauth2client               import file, client, tools
from typing                     import Any, Callable, Dict, List, Optional


SOURCE = {
    'url': 'https://github.com/71/chronos-to-gcalendar',
    'title': 'chronos-to-gcalendar'
}


def connect_calendar() -> Any:
    """
    Connects to Google Calendar and returns a read/write Calendar service.
    
    Service API: https://developers.google.com/resources/api-libraries/documentation/calendar/v3/python/latest
    """
    SCOPES = 'https://www.googleapis.com/auth/calendar'

    store = file.Storage('token.json')
    creds = store.get()
    if not creds or creds.invalid:
        flow = client.flow_from_clientsecrets('credentials.json', SCOPES)
        creds = tools.run_flow(flow, store)
    
    return build('calendar', 'v3', http=creds.authorize(Http()))


def get_events(service, calendarId: str = 'primary', start: Optional[date] = None, end: Optional[date] = None) -> List[Dict[str, Any]]:
    """
    Resolves all the upcoming events for the given calendar for the given period.

    By default, events from today and in the next 14 days are fetched.
    """
    def datestr(date: date) -> str:
        return datetime.combine(date, time()).isoformat() + 'Z'

    if not start:
        start = date.today()
    if not end:
        end = start + timedelta(days=14)

    events = service.events().list(calendarId=calendarId,
                                   timeMin=datestr(start), timeMax=datestr(end),
                                   singleEvents=True, orderBy='startTime').execute()
    
    return events.get('items', [])


def get_schedule(group: str, start: Optional[date] = None, end: Optional[date] = None) -> List[Dict[str, Any]]:
    """
    Resolves all the upcoming classes for the given group for the given period.

    By default, classes from today and in the next 14 days are fetched.
    """
    if not start:
        start = date.today()
    if not end:
        end = start + timedelta(days=14)

    query = f'''
    {{
        classes(name: "{group}", from: "{start}", to: "{end}") {{
            name
            start
            end
            locations {{
                name
            }}
            staff {{
                name
            }}
        }}
    }}
    '''

    data = requests.get('https://chronosql.gregoirege.is', params={'query': query})
    data.raise_for_status()

    json = data.json()

    if not json:
        raise Exception('Could not read data.')
    
    return json['data']['classes']


Course = Dict[str, Any]
CourseFilter = Callable[[Course], bool]

__accept_all_courses = lambda _: True

def upload_schedule(service, calendarId: str, group: str, start: Optional[date] = None, end: Optional[date] = None, filter_course: CourseFilter = __accept_all_courses):
    """
    Uploads the schedule of the given group to the specified calendar.
    """
    def is_match(obj: dict, pattern: dict) -> bool:
        for key in pattern:
            if key not in obj:
                return False

            value = pattern[key]

            if value == obj[key]:
                continue
            
            if type(value) != type(obj[key]):
                return False

            if isinstance(value, dict) and not is_match(obj[key], value):
                return False
            if isinstance(value, str):
                try:
                    if arrow.get(value) == arrow.get(obj[key]):
                        continue
                except:
                    pass

                return False

        return True

    batch = service.new_batch_http_request()

    courses = get_schedule(group, start, end)
    events  = get_events(service, calendarId, start, end)

    if not courses:
        raise Exception('Server did not send back any courses for the {} group.'.format(group))
    

    # Filter out events we don't care about
    events = [ event for event in events if event.get('source') == SOURCE ]


    # Upload new events
    for course in courses:
        body = {
            'summary': course['name'],
            'start': {
                'dateTime': course['start']
            },
            'end': {
                'dateTime': course['end']
            },
            'source': SOURCE
        }

        if course['locations']:
            body['location'] = ', '.join([ loc['name'] for loc in course['locations'] ])
        if course['staff']:
            body['description'] = f'Avec {", ".join([ staff["name"] for staff in course["staff"] ])}.'


        # Find out if this course has already been added
        i, already_exists = 0, False
        
        while i < len(events):
            event = events[i]

            if is_match(event, body):
                # A similar event already exists, so we keep it
                already_exists = True
                events.pop(i)
            else:
                # This event does not match anything, keep it for now
                i += 1

        if not already_exists and filter_course(course):
            batch.add(service.events().insert(calendarId=calendarId, body=body))


    # Remove old events we no longer use in this period
    for event in events:
        batch.add(service.events().delete(calendarId=calendarId, eventId=event['id']))


    # Execute request
    batch.execute()
