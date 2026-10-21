"""Opt-in bounded timing diagnostics; no payloads, SQL text or strategy decisions.

Percentiles are upper bounds from a 1.08-ratio histogram (not exact samples).
Mean/count/max are exact for observed timings. A separate writer flushes every
five seconds; IO failure is recorded and never changes the trading state machine.
"""
from collections import deque
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import queue
import sqlite3
import threading
import time

from ..models import instant, utc


class Timings:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.metrics = {}
        self.events = deque(maxlen=64)
        self.first_failure = None
        self.first_error = None
        self.current = None
        self.maximum_queue = 0
        self.write_error = None
        self.done = threading.Event()
        self.thread = None

    def observe(self, name, milliseconds):
        value = max(0.0,float(milliseconds))
        bucket = min(511,max(0,math.ceil(math.log1p(value)/math.log(1.08))))
        with self.lock:
            item=self.metrics.setdefault(name,dict(count=0,total=0.0,maximum=0.0,bins=[0]*512))
            item['count']+=1; item['total']+=value; item['maximum']=max(item['maximum'],value)
            item['bins'][bucket]+=1
            current = self.current
            if current is not None and current.get('_consumer_thread') == threading.get_ident():
                stage = current.setdefault('stages', {}).setdefault(name, dict(count=0, total_ms=0.0, max_ms=0.0))
                stage['count'] += 1
                stage['total_ms'] += value
                stage['max_ms'] = max(stage['max_ms'], value)

    def call(self,name,fn,*args,**kwargs):
        before=time.perf_counter()
        try: return fn(*args,**kwargs)
        finally: self.observe(name,(time.perf_counter()-before)*1000)

    def complete(self, failed=False):
        item=self.current
        if item is None: return
        self.current=None
        item=dict(item)
        item.pop('_consumer_thread', None)
        item['finished_utc']=utc(datetime.now(timezone.utc))
        item['processing_ms']=(time.perf_counter()-item.pop('_dequeued_mono'))*1000
        item['total_receipt_to_finish_ms']=(instant(item['finished_utc'])-instant(item['received_utc'])).total_seconds()*1000
        item['failed']=bool(failed)
        self.observe(item['kind']+'.total_delay',item['total_receipt_to_finish_ms'])
        self.observe(item['kind']+'.processing',item['processing_ms'])
        with self.lock:
            self.events.append(item)
            if failed and self.first_failure is None: self.first_failure=list(self.events)

    def snapshot(self):
        with self.lock:
            stats={}
            for name,item in self.metrics.items():
                def percentile(fraction):
                    target=math.ceil(item['count']*fraction); count=0
                    for index,n in enumerate(item['bins']):
                        count+=n
                        if count>=target: return min(item['maximum'],1.08**index-1)
                stats[name]=dict(count=item['count'],mean_ms=item['total']/item['count'],
                    p95_ms=percentile(.95),p99_ms=percentile(.99),max_ms=item['maximum'])
            return dict(schema=1,measurements=stats,maximum_queue_length=self.maximum_queue,
                recent_events=list(self.events),first_failure_events=self.first_failure,write_error=self.write_error,
                first_error=self.first_error,
                percentile_method='bounded logarithmic histogram; 1.08 ratio upper bounds',
                limits='Nested engine timings include SQLite; do not sum. No CPU/disk attribution without host tracing.')

    def flush(self):
        try:
            from tools.one_hour_runner.core import atomic_json
            atomic_json(self.path,self.snapshot())
        except Exception as exc:
            self.write_error=type(exc).__name__

    def start(self):
        def write():
            while not self.done.wait(5): self.flush()
            self.flush()
        self.thread=threading.Thread(target=write,name='paper-timing-writer',daemon=False)
        self.thread.start()

    def close(self):
        self.complete()
        self.done.set()
        if self.thread: self.thread.join()


class TimedQueue(queue.Queue):
    def __init__(self,size,timings):
        super().__init__(size)
        self.timings=timings

    def _put(self,item):
        now=datetime.now(timezone.utc)
        received=item[3] if item[0] in {'tick','frame'} else item[1]
        stamp=dict(kind=item[0],received_utc=received,enqueued_utc=utc(now))
        self.timings.observe(item[0]+'.enqueue_after_receipt',(now-instant(received)).total_seconds()*1000)
        super()._put((time.perf_counter(),stamp,item))
        with self.timings.lock:
            self.timings.maximum_queue=max(self.timings.maximum_queue,len(self.queue))

    def _get(self):
        self.timings.complete()
        enqueued,stamp,item=super()._get()
        mono=time.perf_counter(); now=datetime.now(timezone.utc)
        stamp.update(dequeued_utc=utc(now),queue_wait_ms=(mono-enqueued)*1000,
                     queue_length=len(self.queue),_dequeued_mono=mono,
                     _consumer_thread=threading.get_ident())
        self.timings.current=stamp
        self.timings.observe(item[0]+'.queue_wait',stamp['queue_wait_ms'])
        self.timings.observe(item[0]+'.dequeue_after_receipt',(now-instant(stamp['received_utc'])).total_seconds()*1000)
        return item


class MeasuredCursor(sqlite3.Cursor):
    def fetchone(self):
        c=self.connection
        return c.timings.call(c.label+'.sql_fetch',super().fetchone)
    def fetchmany(self,*args):
        c=self.connection
        return c.timings.call(c.label+'.sql_fetch',super().fetchmany,*args)
    def fetchall(self):
        c=self.connection
        return c.timings.call(c.label+'.sql_fetch',super().fetchall)
    def __next__(self):
        c=self.connection
        return c.timings.call(c.label+'.sql_fetch',super().__next__)


class MeasuredConnection(sqlite3.Connection):
    timings=None
    label='db'
    def execute(self,sql,*args,**kwargs):
        if self.timings is None: return super().execute(sql,*args,**kwargs)
        kind='sql_read' if sql.lstrip().upper().startswith(('SELECT','PRAGMA')) else 'sql_write'
        return self.timings.call(self.label+'.'+kind,self.cursor(factory=MeasuredCursor).execute,sql,*args,**kwargs)
    def executemany(self,sql,*args,**kwargs):
        if self.timings is None: return super().executemany(sql,*args,**kwargs)
        return self.timings.call(self.label+'.sql_write',self.cursor(factory=MeasuredCursor).executemany,sql,*args,**kwargs)
    def executescript(self,sql):
        if self.timings is None: return super().executescript(sql)
        return self.timings.call(self.label+'.sql_script',super().executescript,sql)
    def commit(self):
        if self.timings is None: return super().commit()
        return self.timings.call(self.label+'.commit',super().commit)
    def __exit__(self,*args):
        if self.timings is None: return super().__exit__(*args)
        kind='commit' if self.in_transaction and args[0] is None else 'rollback' if self.in_transaction else 'transaction_noop'
        return self.timings.call(self.label+'.'+kind,super().__exit__,*args)


def measured_engine(engine,timings,label):
    if timings is None: return engine
    class Measured:
        def __getattr__(self,name): return getattr(engine,name)
    result=Measured()
    for name in ('on_tick','on_frame','heartbeat','gap','finish','view'):
        original=getattr(engine,name)
        def call(*args,_fn=original,_name=name,**kwargs):
            if _name == 'gap' and len(args) >= 2:
                from ..failure_evidence import safe_path
                with timings.lock:
                    if timings.first_error is None:
                        timings.first_error = dict(code=safe_path(args[1]), occurred_at_utc=args[0], engine=label)
            return timings.call(label+'.'+_name,_fn,*args,**kwargs)
        setattr(result,name,call)
    return result
