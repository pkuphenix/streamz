from collections import deque
from time import time

from tornado import gen
from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.queues import Queue


no_default = '--no-default--'


class Stream(object):
    def __init__(self, child=None, **kwargs):
        self.parents = []
        self.child = child
        if 'loop' in kwargs:
            self.loop = kwargs.get('loop') or IOLoop.current()
        if child:
            self.child.add_parent(self)

    def add_parent(self, other):
        self.parents.append(other)

    def emit(self, x):
        results = [parent.update(x) for parent in self.parents]
        results = [r if type(r) is list else [r] for r in results if r]
        return sum(results, [])

    def map(self, func):
        return map(func, self)

    def filter(self, predicate):
        return filter(predicate, self)

    def scan(self, func, start=no_default):
        return scan(func, self, start=start)

    def buffer(self, n, loop=None):
        return buffer(n, self, loop=loop)

    def partition(self, n):
        return partition(n, self)

    def sliding_window(self, n):
        return sliding_window(n, self)

    def timed_window(self, interval, loop=None):
        return timed_window(interval, self, loop=loop)

    def delay(self, interval, loop=None):
        return delay(interval, self, loop=None)

    def rate_limit(self, interval):
        return rate_limit(interval, self)

    def to_dask(self):
        from .dask import DaskStream
        return DaskStream(self)

    def sink(self, func):
        return Sink(func, self)

    def sink_to_list(self):
        L = []
        Sink(L.append, self)
        return L


class Sink(Stream):
    def __init__(self, func, child):
        self.func = func

        Stream.__init__(self, child)

    def update(self, x):
        result = self.func(x)
        if isinstance(result, gen.Future):
            return result
        else:
            return []


class map(Stream):
    def __init__(self, func, child):
        self.func = func

        Stream.__init__(self, child)

    def update(self, x):
        return self.emit(self.func(x))


class filter(Stream):
    def __init__(self, predicate, child):
        self.predicate = predicate

        Stream.__init__(self, child)

    def update(self, x):
        if self.predicate(x):
            return self.emit(x)
        else:
            return []


class scan(Stream):
    def __init__(self, func, child, start=no_default):
        self.func = func
        self.state = start
        Stream.__init__(self, child)

    def update(self, x):
        if self.state is no_default:
            self.state = x
        else:
            self.state = self.func(self.state, x)
            return self.emit(self.state)


class partition(Stream):
    def __init__(self, n, child):
        self.n = n
        self.buffer = []
        Stream.__init__(self, child)

    def update(self, x):
        self.buffer.append(x)
        if len(self.buffer) == self.n:
            result, self.buffer = self.buffer, []
            return self.emit(tuple(result))
        else:
            return []


class sliding_window(Stream):
    def __init__(self, n, child):
        self.n = n
        self.buffer = deque(maxlen=n)
        Stream.__init__(self, child)

    def update(self, x):
        self.buffer.append(x)
        if len(self.buffer) == self.n:
            return self.emit(tuple(self.buffer))
        else:
            return []


class timed_window(Stream):
    def __init__(self, interval, child, loop=None):
        self.interval = interval
        self.buffer = []
        self.last = gen.moment

        Stream.__init__(self, child, loop=loop)

        self.loop.add_callback(self.cb)

    def update(self, x):
        self.buffer.append(x)
        return self.last

    @gen.coroutine
    def cb(self):
        while True:
            L, self.buffer = self.buffer, []
            self.last = self.emit(L)
            yield self.last
            yield gen.sleep(self.interval)


class delay(Stream):
    def __init__(self, interval, child, loop=None):
        self.interval = interval
        self.queue = Queue()

        Stream.__init__(self, child, loop=loop)

        self.loop.add_callback(self.cb)

    @gen.coroutine
    def cb(self):
        while True:
            last = time()
            x = yield self.queue.get()
            yield self.emit(x)
            duration = self.interval - (time() - last)
            if duration > 0:
                yield gen.sleep(duration)

    def update(self, x):
        return self.queue.put(x)


class rate_limit(Stream):
    def __init__(self, interval, child):
        self.interval = interval
        self.last = 0

        Stream.__init__(self, child)

    @gen.coroutine
    def update(self, x):
        now = time()
        duration = self.interval - (time() - self.last)
        self.last = now
        if duration > 0:
            yield gen.sleep(duration)
        results = yield self.emit(x)
        raise gen.Return(results)


class buffer(Stream):
    def __init__(self, n, child, loop=None):
        self.child = child
        self.queue = Queue(maxsize=n)

        Stream.__init__(self, child, loop=loop)

        self.loop.add_callback(self.cb)

    def update(self, x):
        return self.queue.put(x)

    @gen.coroutine
    def cb(self):
        while True:
            x = yield self.queue.get()
            yield self.emit(x)
