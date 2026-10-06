"""Stream cancellation correctness; scripted waits, no external provider."""

import asyncio

import pytest

from fast_api.app.services.subagent_tree_control import TreeStopRequested, controlled_entries


def test_stop_cancels_waiting_child_and_closes_both_streams():
    async def scenario():
        entered, stop = asyncio.Event(), asyncio.Event()
        closed = []

        class Control:
            async def wait_for_stop(self):
                await stop.wait()

        async def source():
            try:
                yield {"phase": "created"}
                entered.set()
                await asyncio.Event().wait()
                yield {"phase": "must-not-appear"}
            finally:
                closed.append(True)

        stream = controlled_entries(source(), Control())
        assert await anext(stream) == {"phase": "created"}
        task = asyncio.create_task(anext(stream))
        await entered.wait()
        stop.set()
        with pytest.raises(TreeStopRequested, match="user_cancelled"):
            await task
        assert closed == [True]
        await stream.aclose()

    asyncio.run(scenario())


def test_normal_completion_closes_watcher_without_claiming_stop():
    async def scenario():
        watcher_closed = []

        class Control:
            async def wait_for_stop(self):
                try:
                    await asyncio.Event().wait()
                finally:
                    watcher_closed.append(True)

        async def source():
            yield {"phase": "completed"}

        assert [row async for row in controlled_entries(source(), Control())] == [
            {"phase": "completed"}
        ]
        assert watcher_closed == [True]

    asyncio.run(scenario())


def test_control_failure_closes_waiting_child_instead_of_continuing():
    async def scenario():
        entered = asyncio.Event()
        closed = []

        class Control:
            async def wait_for_stop(self):
                await entered.wait()
                raise RuntimeError("scripted control failure")

        async def source():
            try:
                entered.set()
                await asyncio.Event().wait()
                yield {}
            finally:
                closed.append(True)

        with pytest.raises(RuntimeError, match="control failure"):
            async for _ in controlled_entries(source(), Control()):
                pass
        assert closed == [True]

    asyncio.run(scenario())


def test_consumer_close_cancels_watcher_and_closes_source():
    async def scenario():
        closed = []

        class Control:
            async def wait_for_stop(self):
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.append("watcher")

        async def source():
            try:
                yield {}
            finally:
                closed.append("source")

        stream = controlled_entries(source(), Control())
        await anext(stream)
        await stream.aclose()
        assert sorted(closed) == ["source", "watcher"]

    asyncio.run(scenario())


def test_parent_task_cancellation_propagates_and_releases_control_wait():
    async def scenario():
        entered = asyncio.Event()
        closed = []

        class Control:
            async def wait_for_stop(self):
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.append("watcher")

        async def source():
            try:
                entered.set()
                await asyncio.Event().wait()
                yield {}
            finally:
                closed.append("source")

        async def consume():
            async for _ in controlled_entries(source(), Control()):
                pass

        task = asyncio.create_task(consume())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sorted(closed) == ["source", "watcher"]

    asyncio.run(scenario())
