"""Versioned XNYS schedule, not a Kiwoom API/calendar guarantee."""
from datetime import date, timedelta
from ..models import Session, utc


class ExchangeCalendar:
    def __init__(self):
        import exchange_calendars
        self.calendar = exchange_calendars.get_calendar("XNYS")
        self.source = "exchange_calendars-" + exchange_calendars.__version__ + ":XNYS"

    def sessions(self, start, end):
        frame = self.calendar.schedule.loc[start:end]
        if start < self.calendar.first_session.date().isoformat() or end > self.calendar.last_session.date().isoformat():
            raise ValueError("Calendar range unavailable")
        return tuple(Session(index.date().isoformat(), utc(row['open'].to_pydatetime()),
                             utc(row['close'].to_pydatetime())) for index, row in frame.iterrows())

    def session(self, day):
        rows = self.sessions(day, day)
        return rows[0] if rows else None

    def history(self, day, count):
        end = date.fromisoformat(day) - timedelta(days=1)
        rows = self.sessions((end-timedelta(days=count*3+30)).isoformat(), end.isoformat())
        return tuple(reversed(rows[-count:]))
