"""Tests for the Playwright Python"""

import asyncio
import contextlib
import json
import re
import urllib.parse

import pytest
from playwright.async_api import expect
from tornado.escape import url_escape
from tornado.httputil import url_concat

from jupyterhub import orm, roles, scopes
from jupyterhub.tests.utils import public_host, public_url, ujoin
from jupyterhub.utils import url_escape_path, url_path_join


async def login(browser, username, password):
    """filling the login form by user and pass_w parameters and iniate the login"""

    await browser.get_by_label("Username:").click()
    await browser.get_by_label("Username:").fill(username)
    await browser.get_by_label("Password:").click()
    await browser.get_by_label("Password:").fill(password)
    await browser.get_by_role("button", name="Sign in").click()


async def test_open_login_page(app, browser):
    login_url = url_path_join(public_host(app), app.hub.base_url, "login")
    await browser.goto(login_url)
    await expect(browser).to_have_url(re.compile(r".*/login"))
    await expect(browser).to_have_title("JupyterHub")
    form = browser.locator('//*[@id="login-main"]/form')
    await expect(form).to_be_visible()
    await expect(form.locator('//h1')).to_have_text("Sign in")


async def test_submit_login_form(app, browser, user):

    login_url = url_path_join(public_host(app), app.hub.base_url, "login")
    await browser.goto(login_url)
    await login(browser, user.name, password=user.name)
    expected_url = ujoin(public_url(app), f"/user/{user.name}/")
    await expect(browser).to_have_url(expected_url)


@pytest.mark.parametrize(
    'url, params, redirected_url, form_action',
    [
        (
            # spawn?param=value
            # will encode given parameters for an unauthenticated URL in the next url
            # the next parameter will contain the app base URL (replaces BASE_URL in tests)
            'spawn',
            [('param', 'value')],
            '/hub/login?next={{BASE_URL}}hub%2Fspawn%3Fparam%3Dvalue',
            '/hub/login?next={{BASE_URL}}hub%2Fspawn%3Fparam%3Dvalue',
        ),
        (
            # login?param=fromlogin&next=encoded(/hub/spawn?param=value)
            # will drop parameters given to the login page, passing only the next url
            'login',
            [('param', 'fromlogin'), ('next', '/hub/spawn?param=value')],
            '/hub/login?param=fromlogin&next=%2Fhub%2Fspawn%3Fparam%3Dvalue',
            '/hub/login?next=%2Fhub%2Fspawn%3Fparam%3Dvalue',
        ),
        (
            # login?param=value&anotherparam=anothervalue
            # will drop parameters given to the login page, and use an empty next url
            'login',
            [('param', 'value'), ('anotherparam', 'anothervalue')],
            '/hub/login?param=value&anotherparam=anothervalue',
            '/hub/login?next=',
        ),
        (
            # login
            # simplest case, accessing the login URL, gives an empty next url
            'login',
            [],
            '/hub/login',
            '/hub/login?next=',
        ),
    ],
)
async def test_open_url_login(
    app,
    browser,
    url,
    params,
    redirected_url,
    form_action,
    user,
):

    login_url = url_path_join(public_host(app), app.hub.base_url, url)
    await browser.goto(login_url)
    url_new = url_path_join(public_host(app), app.hub.base_url, url_concat(url, params))
    print(url_new)
    await browser.goto(url_new)
    redirected_url = redirected_url.replace(
        '{{BASE_URL}}', url_escape_path(app.base_url)
    )
    form_action = form_action.replace('{{BASE_URL}}', url_escape(app.base_url))

    form = browser.locator('//*[@id="login-main"]/form')

    # verify title / url
    await expect(browser).to_have_title("JupyterHub")
    f_string = re.escape(f"{form_action}")
    await expect(form).to_have_attribute('action', re.compile('.*' + f_string))

    # login in with params
    await login(browser, user.name, password=user.name)
    # verify next url + params
    if url_escape(app.base_url) in form_action:
        await expect(browser).to_have_url(re.compile(".*param=value"))
    elif "next=%2Fhub" in form_action:
        escaped_string = re.escape('spawn?param=value')
        pattern = re.compile('.*' + escaped_string)
        await expect(browser).to_have_url(re.compile(pattern))
        await expect(browser).not_to_have_url(re.compile(".*/user/.*"))
    else:
        await expect(browser).to_have_url(re.compile(".*/user/" + f"{user.name}/"))


@pytest.mark.parametrize(
    "username, pass_w",
    [
        (" ", ""),
        ("user", ""),
        (" ", "password"),
        ("user", "password"),
    ],
)
async def test_login_with_invalid_credantials(app, browser, username, pass_w):
    login_url = url_path_join(public_host(app), app.hub.base_url, "login")
    await browser.goto(login_url)
    await login(browser, username, pass_w)
    locator = browser.locator("p.login_error")
    expected_error_message = "Invalid username or password"
    # verify error message displayed and user stays on login page
    await expect(locator).to_be_visible()
    await expect(locator).to_contain_text(expected_error_message)
    await expect(browser).to_have_url(re.compile(".*/hub/login"))


# SPAWNING


async def open_spawn_pending(app, browser, user):
    url = url_path_join(
        public_host(app),
        url_concat(
            url_path_join(app.base_url, "login"),
            {"next": url_path_join(app.base_url, "hub/home")},
        ),
    )
    await browser.goto(url)
    await login(browser, user.name, password=user.name)
    url_spawn = url_path_join(
        public_host(app), app.hub.base_url, '/spawn-pending/' + user.name
    )
    await browser.goto(url_spawn)
    await expect(browser).to_have_url(url_spawn)


async def test_spawn_pending_server_not_started(
    app, browser, no_patience, user, slow_spawn
):
    # first request, no spawn is pending
    # spawn-pending shows button linking to spawn
    await open_spawn_pending(app, browser, user)
    # on the page verify the button and expected information
    expected_heading = "Server not running"
    heading = browser.locator('//div[@class="text-center"]').get_by_role("heading")
    await expect(heading).to_have_text(expected_heading)
    await expect(heading).to_be_visible()
    expected_button_name = "Launch Server"
    launch_btn = browser.locator('//div[@class="text-center"]').get_by_role("button")
    await expect(launch_btn).to_have_text(expected_button_name)
    await expect(launch_btn).to_have_id("start")
    await expect(launch_btn).to_be_enabled()
    await expect(launch_btn).to_have_count(1)
    f_string = re.escape(f"/hub/spawn/{user.name}")
    await expect(launch_btn).to_have_attribute('href', re.compile('.*' + f_string))


@pytest.mark.xfail(reason="flaky on CI")
async def test_spawn_pending_progress00(
    app, browser, no_patience, user, slow_spawn, slow_mo=10000
):
    """verify that the server process messages are showing up to the user
    when the server is going to start up"""
    # begin starting the server
    # await api_request(app, f"/users/{user.name}/server", method="post")
    # visit the spawn-pending page

    await open_spawn_pending(app, browser, user)
    launch_btn = browser.locator('//div[@class="text-center"]').get_by_role(
        "button", name="Launch Server"
    )
    await expect(launch_btn).to_be_enabled()

    await launch_btn.click()

    while '/spawn-pending/' in browser.url:
        progress_message = browser.locator("#progress-message").inner_text()
        # checking text messages that the server is starting to up

        # checking progress of the servers starting (messages, % progress and events log))
        progress_bar = browser.locator('#progress-bar')
        progress = browser.locator("#progress-message")
        logs = browser.locator('#progress-log-event')
        percent = await progress_bar.get_attribute('style')
        percent = percent.split(';')[0].split(':')[1].strip()
        # percent_el=await browser.wait_for_selector('#progress-bar[aria-valuenow]')
        # percent=int(await percent_el.get_attribute('aria-valuenow').all())
        logs_list = [log.text for log in await logs.all() if log.text]
        if await progress_message == "":
            assert percent == "0%"
            assert len(logs_list) == 0
        elif progress_message == "Server requested":
            assert percent == "0%"
            assert len(logs_list) == 1
            assert str(logs_list[0]) == "Server requested"
        elif progress_message == "Spawning server...":
            assert percent == "50%"
            assert len(logs_list) == 2
            assert str(logs_list[0]) == "Server requested"
            assert str(logs_list[1]) == "Spawning server..."
        elif "Server ready at" in progress_message:
            assert (
                f"Server ready at {app.base_url}user/{user.name}/" in progress_message
            )
            assert percent == "100%"
            assert len(logs_list) == 3
            assert str(logs_list[0]) == "Server requested"
            assert str(logs_list[1]) == "Spawning server..."
            assert (
                str(logs_list[2]) == f"Server ready at {app.base_url}user/{user.name}/"
            )
            assert str(logs_list[2]) == progress_message


@pytest.mark.xfail(reason="flaky on CI")
async def test_spawn_pending_progress0(
    app, browser, no_patience, user, slow_spawn, slow_mo=10000
):
    """verify that the server process messages are showing up to the user
    when the server is going to start up"""
    # begin starting the server
    # await api_request(app, f"/users/{user.name}/server", method="post")
    # visit the spawn-pending page

    await open_spawn_pending(app, browser, user)
    launch_btn = browser.locator('//div[@class="text-center"]').get_by_role(
        "button", name="Launch Server"
    )
    await expect(launch_btn).to_be_enabled()

    await launch_btn.click()
    while '/spawn-pending/' in browser.url:
        # Verify the progress messages
        progress_message = browser.locator("#progress-message")
        progress_messages = [
            "Server requested",
            "Spawning server...",
            "Server ready at ",
        ]
        for message in progress_messages:
            await browser.wait_for_selector(f"#progress-message:has-text('{message}')")

        # Verify the progress log events
        progress_log = await browser.locator("#progress-log")
        events = await progress_log.locator_all(".progress-log-event")
        assert len(events) >= len(progress_messages)
        for event, message in zip(events, progress_messages):
            assert message in await event.inner_text()
    # await browser.wait_for_selector('#progress-bar[aria-valuenow="0"]')
    """
    # Get the progress bar elements
    progress_bar = await browser.query_selector('#progress-bar')
    sr_progress = await browser.query_selector('#sr-progress')
    progress_message = await browser.query_selector('#progress-message')

    # Get the progress bar value
    progress_bar_value = await progress_bar.get_attribute('aria-valuenow')

    # Get the progress message text
    progress_message_text = await progress_message.text_content().strip()

    # Get the screen reader progress text
    sr_progress_text = await sr_progress.text_content().strip()

    # Verify the progress bar value and message
    if progress_bar_value == '0' and progress_message_text == '' and sr_progress_text == '0% Complete':
        print('Progress bar is initialized correctly')
    else:
        print('Progress bar is not initialized correctly')"""


@pytest.mark.xfail(reason="flaky on CI")
async def test_spawn_pending_progress10(app, browser, no_patience, user, slow_spawn):
    """verify that the server process messages are showing up to the user
    when the server is going to start up"""
    # begin starting the server
    # visit the spawn-pending page

    await open_spawn_pending(app, browser, user)
    launch_btn = browser.locator('//div[@class="text-center"]').get_by_role(
        "button", name="Launch Server"
    )
    await expect(launch_btn).to_be_enabled()

    await launch_btn.click()

    # use context manager to synchronize access to progress_message
    async with contextlib.AsyncExitStack() as stack:
        progress_message_lock = stack.enter_async_context(asyncio.Lock())
        # progress_message_lock = stack.enter_async_context(asyncio.Lock())
        progress_message = None

        # loop until progress bar disappears
        while '/spawn-pending/' in browser.url:
            # get the latest progress message
            new_progress_message = await browser.locator(
                "#progress-message"
            ).inner_text()

            async with progress_message_lock:
                # check if progress message has changed
                if new_progress_message != progress_message:
                    progress_message = new_progress_message

                    # checking text messages that the server is starting to up
                    expected_messages = [
                        "Server requested",
                        "Spawning server...",
                        f"Server ready at {app.base_url}user/{user.name}/",
                    ]

                    assert progress_message in expected_messages

                    # checking progress of the servers starting (messages, % progress and events log))
                    progress_bar = browser.locator('#progress-bar')
                    percent_el = await browser.wait_for_selector(
                        '#progress-bar[aria-valuenow]'
                    )
                    percent = int(await percent_el.get_attribute('aria-valuenow'))
                    logs = browser.locator('#progress-log-event')
                    logs_list = [log.text for log in await logs.all() if log.text]

                    if logs_list:
                        # race condition: progress_message _should_
                        # be the last log message, but it _may_ be the next one
                        assert progress_message

                    assert logs_list == expected_messages[: len(logs_list)]

            # wait for some time before checking again
            await asyncio.sleep(0.2)

        # wait for the launch button to become detached
        await launch_btn.wait_for(state='detached')


@pytest.mark.xfail(reason="flaky on CI")
async def test_spawn_pending_progress11(app, browser, no_patience, user, slow_spawn):
    """verify that the server process messages are showing up to the user
    when the server is going to start up"""
    # begin starting the server
    # visit the spawn-pending page

    await open_spawn_pending(app, browser, user)
    launch_btn = browser.locator('//div[@class="text-center"]').get_by_role(
        "button", name="Launch Server"
    )
    await expect(launch_btn).to_be_enabled()

    await launch_btn.click()

    # use context manager to synchronize access to progress_message
    async with contextlib.AsyncExitStack() as stack:
        progress_message_lock = asyncio.Lock()
        progress_message = ""
        percent_lock = asyncio.Lock()
        percent = 0

        # loop until progress bar disappears
        while '/spawn-pending/' in browser.url:
            # get the latest progress message
            new_progress_message = await browser.locator(
                "#progress-message"
            ).inner_text()
            percent_el = await browser.wait_for_selector('#progress-bar[aria-valuenow]')
            new_percent = int(await percent_el.get_attribute('aria-valuenow'))
            print("progress_message")
            print(progress_message)
            print("new_progress_message")
            print(new_progress_message)

            print(percent)
            async with progress_message_lock and percent_lock:
                # check if progress message has changed
                if new_progress_message != progress_message:
                    progress_message = new_progress_message

                    # checking text messages that the server is starting to up
                    expected_messages = [
                        "Server requested",
                        "Spawning server...",
                        f"Server ready at {app.base_url}user/{user.name}/",
                    ]

                    assert progress_message in expected_messages

                    # checking progress of the servers starting (messages, % progress and events log))
                    progress_bar = browser.locator('#progress-bar')
                    percent_el = await browser.wait_for_selector(
                        '#progress-bar[aria-valuenow]'
                    )
                    if new_percent != percent:
                        percent = new_percent
                        assert percent in [0, 50, 100]

                    logs = browser.locator('#progress-log-event')
                    logs_list = [log.text for log in await logs.all() if log.text]

                    if logs_list:
                        # race condition: progress_message _should_
                        # be the last log message, but it _may_ be the next one
                        assert progress_message

                    assert logs_list == expected_messages[: len(logs_list)]
                    print(len(logs_list))
            # wait for some time before checking again
            await asyncio.sleep(0.2)

        # wait for the launch button to become detached
        await launch_btn.wait_for(state='detached')


@pytest.mark.xfail(reason="flaky on CI")
async def test_spawn_pending_progress12(app, browser, no_patience, user, slow_spawn):
    """verify that the server process messages are showing up to the user
    when the server is going to start up"""
    # begin starting the server
    # visit the spawn-pending page

    await open_spawn_pending(app, browser, user)
    launch_btn = browser.locator('//div[@class="text-center"]').get_by_role(
        "button", name="Launch Server"
    )
    await expect(launch_btn).to_be_enabled()

    async with browser.expect_navigation(url=f"**/user/{user.name}/"):
        await launch_btn.click()

        # use context manager to synchronize access to progress_message
        async with contextlib.AsyncExitStack() as stack:
            progress_message_lock = asyncio.Lock()
            progress_message = ""
            percent_lock = asyncio.Lock()
            percent = 0
            logs_list = []
            data = {
                "progress_message": progress_message,
                "percent": percent,
                "logs_list": logs_list,
            }

            # write the dictionary to a JSON file
            with open("test_data.json", "w") as f:
                json.dump(data, f)
            print(data)
            # loop until progress bar disappears
            while '/spawn-pending/' in browser.url:
                # get the latest progress message
                # try:
                new_progress_message = await browser.locator(
                    "#progress-message"
                ).inner_text()
                percent_el = await browser.wait_for_selector(
                    '#progress-bar[aria-valuenow]'
                )
                # percent_el.is_visible()
                new_percent = int(await percent_el.get_attribute('aria-valuenow'))
                print("progress_message")
                print(progress_message)
                print("new_progress_message")
                print(new_progress_message)

                print(percent)
                # except ElementNotInteractableError:

                # print(print("Element is not interactable"))
                async with progress_message_lock and percent_lock:
                    # check if progress message has changed
                    if new_progress_message != progress_message:
                        progress_message = new_progress_message

                        # checking text messages that the server is starting to up
                        expected_messages = [
                            "Server requested",
                            "Spawning server...",
                            f"Server ready at {app.base_url}user/{user.name}/",
                        ]

                        assert progress_message in expected_messages

                        # checking progress of the servers starting (messages, % progress and events log))
                        progress_bar = browser.locator('#progress-bar')
                        percent_el = await browser.wait_for_selector(
                            '#progress-bar[aria-valuenow]'
                        )
                        if new_percent != percent:
                            percent = new_percent
                            assert percent in range(0, 100)

                        logs = browser.locator('#progress-log-event')
                        new_logs_list = [
                            log.text for log in await logs.all() if log.text
                        ]

                        if new_logs_list:
                            # race condition: progress_message _should_
                            # be the last log message, but it _may_ be the next one
                            assert progress_message

                        assert new_logs_list == expected_messages[: len(new_logs_list)]
                        print(len(new_logs_list))

                        # save the progress message, percent and logs list to a dictionary
                        data = {
                            "progress_message": progress_message,
                            "percent": percent,
                            "logs_list": logs_list,
                        }

                        # write the dictionary to a JSON file
                        with open("test_data.json", "w") as f:
                            json.dump(data, f)

                        # update the logs_list
                        logs_list = new_logs_list
                        # print(data)
                # wait for some time before checking again
                await asyncio.sleep(0.001)

        # wait for the
        # await launch_btn.wait_for(state='detached')


@pytest.mark.xfail(reason="flaky on CI")
async def test_spawn_pending_progress33(app, browser, no_patience, user, slow_spawn):
    """verify that the server process messages are showing up to the user
    when the server is going to start up"""
    # begin starting the server
    # await api_request(app, f"/users/{user.name}/server", method="post")
    # visit the spawn-pending page

    await open_spawn_pending(app, browser, user)
    launch_btn = browser.locator('//div[@class="text-center"]').get_by_role(
        "button", name="Launch Server"
    )
    await expect(launch_btn).to_be_enabled()

    await launch_btn.click()
    # time.sleep(1000)
    while '/spawn-pending/' in browser.url:
        progress_message = await browser.run(
            lambda: browser.locator('#progress-message').inner_text()
        )
        # progress_message = await browser.locator('#progress-message').inner_text()
        assert "" in progress_message
        # new_progress_message = progress_message

        new_progress_message = await browser.run(
            lambda: browser.wait_for_selector('#progress-message').wait_for(
                lambda element, text: text in element.text_content(),
                args=['Server requested'],
            )
        )
        # new_progress_message=await browser.locator('#progress-message').wait_for(lambda element: 'Server requested' in element.text_content())
        # new_progress_message.wait_for(has_text:"Server requested")
        assert "Server requested" in new_progress_message
        assert "Server requested" in progress_message
        assert "Spawning server..." in progress_message
        assert "Server ready at" in progress_message

        # verify logs
        progress_log = await browser.locator('#progress-log')
        logs = await progress_log.locator('.progress-log-event').inner_text()
        expected_logs = [
            "Server requested",
            "Spawning server...",
            f"Server ready at {app.base_url}user/{user.name}/",
        ]
        assert logs == "\n".join(expected_logs)

        # verify percent
        progress_bar = await browser.locator('#progress-bar')
        percent = await progress_bar.get_attribute('aria-valuenow')
        assert percent == "100"
        # await launch_btn.wait_for(state='detached')


@pytest.mark.xfail(reason="flaky on CI")
async def test_spawn_pending_progress3(app, browser, no_patience, user, slow_spawn):
    """verify that the server process messages are showing up to the user
    when the server is going to start up"""
    # begin starting the server
    # await api_request(app, f"/users/{user.name}/server", method="post")
    # visit the spawn-pending page

    await open_spawn_pending(app, browser, user)
    launch_btn = browser.locator('//div[@class="text-center"]').get_by_role(
        "button", name="Launch Server"
    )
    await expect(launch_btn).to_be_enabled()

    await launch_btn.click()
    # time.sleep(1000)
    while '/spawn-pending/' in browser.url:
        # save the progress message, percent and logs list to a dictionary

        progress_message = await browser.locator("#progress-message").inner_text()
        # checking text messages that the server is starting to up

        # checking progress of the servers starting (messages, % progress and events log))
        progress_bar = browser.locator('#progress-bar')
        progress = await browser.locator("#progress-message").all()
        logs = browser.locator('#progress-log-event')
        percent_el = await browser.wait_for_selector('#progress-bar[aria-valuenow]')
        percent = int(await percent_el.get_attribute('aria-valuenow'))
        logs_list = [log.text for log in await logs.all() if log.text]
        expected_messages = [
            "Server requested",
            "Spawning server...",
            f"Server ready at {app.base_url}user/{user.name}/",
        ]
        data = {
            "progress_message": progress_message,
            "percent": percent,
            "logs_list": logs_list,
        }

        # write the dictionary to a JSON file
        with open("test_data.json", "w") as f:
            json.dump(data, f)
        print(data)

        if progress_message:
            assert progress_message in expected_messages
        if logs_list:
            # race condition: progress_message _should_
            # be the last log message, but it _may_ be the next one
            assert progress_message

        assert logs_list == expected_messages[: len(logs_list)]
        # await asyncio.sleep(0.2)
        await launch_btn.wait_for(state='detached')
        # await browser.wait_for_selector_to_be_removed(launch_btn)
        # await launch_btn.wait_for_element_state('detached')
        # print(len(percent))

        # percent = await progress_bar.get_attribute('style')
        # percent = percent.split(';')[0].split(':')[1].strip()

        # only include non-empty log messages
        # avoid partially-created elements
        # logs_list = [log.text for log in await logs.all() if log.text]
        # message_list = [progress_message for message in await progress.all() if progress_message]
        # per_list = [percent for per in  percent if percent]
        # print(logs_list)
        # print(message_list)
        # if not browser.locator('#progress-bar').is_visible():
        # break
        # print(per_list)
        """# Check both percent and progress_message
        if percent < 50:
            assert len(logs_list) <2
            assert await progress_message == "Server requested"
        elif percent <100:
            assert len(logs_list) == 2
            assert logs_list[0] == "Spawning server..."
        elif percent == 100:
            assert len(logs_list) > 2
            assert logs_list[-1] == f"Server ready at {app.base_url}user/{user.name}/" """
        """
        # Check progress_message if percent is not yet updated
        if percent < 50:
            assert await progress_message == "Server requested"
        elif percent<100:
            assert await progress_message == "Spawning server..."
        elif percent == 100:
            assert await progress_message == f"Server ready at {app.base_url}user/{user.name}/" """


# TODO: finish
@pytest.mark.xfail(reason="flaky on CI")
async def test_spawn_pending_progress2(app, browser, no_patience, user, slow_spawn):
    """verify that the server process messages are showing up to the user
    when the server is going to start up"""
    # begin starting the server
    # await api_request(app, f"/users/{user.name}/server", method="post")
    # visit the spawn-pending page

    await open_spawn_pending(app, browser, user)
    launch_btn = browser.locator('//div[@class="text-center"]').get_by_role(
        "button", name="Launch Server"
    )
    await expect(launch_btn).to_be_enabled()

    await launch_btn.click(force=True)

    await browser.wait_for_selector('text=Server requested')
    while '/spawn-pending/' in browser.url:

        # await browser.locator(":nth-match(:text('Server requested'), 1)").wait_for()
        progress_bar = browser.locator('#progress-bar')
        await progress_bar.is_visible()
        # FIXME: reliability may be due to page changing between `find_element` calls
        # maybe better if we get HTML once and parse with beautifulsoup
        progress_message = browser.locator("#progress-message").inner_text()
        # checking progress of the servers starting (messages, % progress and events log))

        progress_bar = browser.locator('#progress-bar')
        progress = browser.locator("#progress-message")
        logs = browser.locator('#progress-log-event')
        percent = await progress_bar.get_attribute('style')
        percent = percent.split(';')[0].split(':')[1].strip()
        # only include non-empty log messages
        # avoid partially-created elements
        logs_list = [log.text for log in await logs.all() if log.text]

        expected_messages = [
            "Server requested",
            "Spawning server...",
            f"Server ready at {app.base_url}user/{user.name}/",
        ]

        if progress_message:
            assert await progress_message in expected_messages
        if logs_list:
            # race condition: progress_message _should_
            # be the last log message, but it _may_ be the next one
            assert await progress_message
            assert logs_list == expected_messages[: len(logs_list)]
        """
        if len(logs_list) < 2:
            assert percent == "0%"
        elif len(logs_list) == 2:
            assert percent == "50%"
        elif len(logs_list) >= 3:
            assert percent == "100%"
            """

    # assert await progress_bar.get_attribute('aria-valuenow') == '0'
    # assert await progress_bar.get_attribute('style') == 'width: 0%;'
    # wait for event log to appear and check initial message
    # event_log = browser.locator('#progress-log')
    # await event_log.click()

    # time.sleep(100)
    # assert await event_log.locator("nth=0").inner_text() == 'Server requested'
    """   
    while '/spawn-pending/' in browser.url:
        progress_percent = await browser.wait_for_selector('#sr-progress').inner_text().all()
        #progress_log = await browser.get_by_text("Server requested").wait_for()
        #progress_log = await browser.locator('#progress-log:visible')
        #progress_log =  await browser.locator('#progress-log-event').all()
        #progress_list = [progress_list.append(await log.inner_text()) for log in progress_log]
        print(progress_percent)
        #progress_logs= await progress_log.all()"""

    # time.sleep(1000)
    """<div class="text-center">
     
      <div class="progress">
        <div id="progress-bar" class="progress-bar" role="progressbar" aria-valuenow="50" aria-valuemin="0" aria-valuemax="100" style="width: 50%;">
          <span class="sr-only">
            <span id="sr-progress">50%</span> Complete</span>
        </div>
      </div>
      <p id="progress-message">Spawning server...</p>
    </div>"""

    """<div class="col-md-8 col-md-offset-2">
      <details id="progress-details">
        <summary>Event log</summary>
        <div id="progress-log">
            <div class="progress-log-event">Server requested</div>
            <div class="progress-log-event">Spawning server...</div></div>
      </details>
    </div>"""
    # await browser.get_by_text("Event log").inner_text().wait_for()
    # server_requested_message = await progress_log.locator('.progress-log-event').filter(has_text='Server requested').wait_for()
    # progress_percent = await browser.wait_for_selector('#sr-progress')
    # progress_percents = await progress_percent.inner_text()
    # log =browser.wait_for_selector('#progress-log-event')
    # print(progress_percents)

    # verify initial progress bar state
    # progress_bar = await browser.wait_for_selector('.progress-bar')
    # progress_text = await browser.wait_for_selector('#sr-progress')
    # assert await progress_bar.get_attribute('aria-valuenow') == '0'
    # assert await progress_text.inner_text() == '0%'

    # wait for the first progress update
    # await browser.wait_for_selector('.progress-log-event')
    # progress_bar = await browser.wait_for_selector('.progress-bar')
    # progress_text = await browser.wait_for_selector('#sr-progress')
    # assert await progress_bar.get_attribute('aria-valuenow') == '0'
    # assert await progress_text.inner_text() == '0%'
    # time.sleep(1000)
    # simulate progress updates
    """progress_url = url_path_join(
        public_host(app), app.hub.base_url, f"api/users/{user.name}/server/progress")
    await browser.route(progress_url, lambda route: route.continue_())
    # click the Launch Server button and wait for progress updates
    await launch_btn.click()

    progress_log = browser.locator('#progress-log')
    evt = await browser.wait_for_event('message', timeout=10)
    await expect(evt.data).to_contain('Server requested')
    await expect(evt.data).to_contain('"progress":0')
    evt = await browser.wait_for_event('message', timeout=10)
    await expect(evt.data).to_contain('Spawning server...')
    await expect(evt.data).to_contain('"progress":50')
    evt = await browser.wait_for_event('message', timeout=10)
    await expect(evt.data).to_contain('Server ready at')
    await expect(evt.data).to_contain('"progress":100')"""


async def test_spawn_pending_server_ready(app, browser, user):
    """verify that after a successful launch server via the spawn-pending page
    the user should see two buttons on the home page"""

    await open_spawn_pending(app, browser, user)
    launch_btn = browser.get_by_role("button", name="Launch Server")
    await launch_btn.click()
    await browser.wait_for_selector("button", state="detached")
    home_page = url_path_join(public_host(app), ujoin(app.base_url, "hub/home"))
    await browser.goto(home_page)
    await browser.wait_for_load_state("domcontentloaded")
    # checking that server is running and two butons present on the home page
    stop_start_btns = browser.locator('//div[@class="text-center"]').get_by_role(
        "button"
    )
    expected_btns_name = ["Stop My Server", "My Server"]
    await expect(stop_start_btns).to_have_count(2)
    await expect(stop_start_btns).to_have_text(expected_btns_name)
    await expect(stop_start_btns.nth(0)).to_have_id("stop")
    await expect(stop_start_btns.nth(1)).to_have_id("start")


# HOME PAGE


async def open_home_page(app, browser, user):
    """function to open the home page"""

    home_page = url_escape(app.base_url) + "hub/home"
    url = url_path_join(public_host(app), app.hub.base_url, "/login?next=" + home_page)
    await browser.goto(url)
    await login(browser, user.name, password=str(user.name))
    await expect(browser).to_have_url(re.compile(".*/hub/home"))


async def test_start_button_server_not_started(app, browser, user):
    """verify that when server is not started one button is availeble,
    after starting 2 buttons are available"""
    await open_home_page(app, browser, user)
    # checking that only one button is presented
    start_stop_btns = browser.locator('//div[@class="text-center"]').get_by_role(
        "button"
    )
    expected_btn_name = "Start My Server"
    await expect(start_stop_btns).to_be_enabled()
    await expect(start_stop_btns).to_have_count(1)
    await expect(start_stop_btns).to_have_text(expected_btn_name)
    f_string = re.escape(f"/hub/spawn/{user.name}")
    await expect(start_stop_btns).to_have_attribute('href', re.compile('.*' + f_string))

    # Start server via clicking on the Start button
    await start_stop_btns.click()
    # return to Home page
    next_url = url_path_join(public_host(app), app.base_url, '/hub/home')
    await browser.goto(next_url)
    # verify that 2 buttons are displayed on the home page
    await expect(start_stop_btns).to_have_count(2)
    expected_btns_names = ["Stop My Server", "My Server"]
    await expect(start_stop_btns).to_have_text(expected_btns_names)
    [
        await expect(start_stop_btn).to_be_enabled()
        for start_stop_btn in await start_stop_btns.all()
    ]
    f_string = re.escape(f"/user/{user.name}")
    await expect(start_stop_btns.nth(1)).to_have_attribute(
        'href', re.compile('.*' + f_string)
    )
    await expect(start_stop_btns.nth(0)).to_have_id("stop")
    await expect(start_stop_btns.nth(1)).to_have_id("start")


# TOKEN PAGE


async def open_token_page(app, browser, user):
    """function to open the token page"""

    token_page = url_escape(app.base_url) + "hub/token"
    url = url_path_join(public_host(app), app.hub.base_url, "/login?next=" + token_page)
    await browser.goto(url)
    await login(browser, user.name, password=str(user.name))
    await expect(browser).to_have_url(re.compile(".*/hub/token"))


async def test_token_request_form_and_panel(app, browser, user):
    """verify elements of the request token form"""

    await open_token_page(app, browser, user)
    request_btn = browser.locator('//div[@class="text-center"]').get_by_role("button")
    expected_btn_name = 'Request new API token'
    # check if the request token button is enabled
    # check the buttons name
    await expect(request_btn).to_be_enabled()
    await expect(request_btn).to_have_text(expected_btn_name)
    # check that the field is enabled and editable and empty by default
    field_note = browser.get_by_label('Note')
    await expect(field_note).to_be_editable()
    await expect(field_note).to_be_enabled()
    await expect(field_note).to_be_empty()

    # check the list of tokens duration
    dropdown = browser.locator('#token-expiration-seconds')
    options = await dropdown.locator('option').all()
    expected_values_in_list = {
        '1 Hour': '3600',
        '1 Day': '86400',
        '1 Week': '604800',
        'Never': '',
    }
    actual_values = {
        await option.text_content(): await option.get_attribute('value')
        for option in options
    }
    assert actual_values == expected_values_in_list
    # get the value of the 'selected' attribute of the currently selected option
    selected_value = dropdown.locator('option[selected]')
    await expect(selected_value).to_have_text("Never")

    # verify that "Your new API Token" panel shows up with the new API token
    await request_btn.click()
    await browser.wait_for_load_state("load")
    expected_panel_token_heading = "Your new API Token"
    token_area = browser.locator('#token-area')
    await expect(token_area).to_be_visible()
    token_area_heading = token_area.locator('//div[@class="panel-heading"]')
    await expect(token_area_heading).to_have_text(expected_panel_token_heading)
    token_result = browser.locator('#token-result')
    await expect(token_result).not_to_be_empty()
    await expect(token_area).to_be_visible()
    # verify that "Your new API Token" panel is hidden after refresh the page
    await browser.reload(wait_until="load")
    await expect(token_area).to_be_hidden()
    api_token_table_area = browser.locator('//div[@class="row"]').nth(2)
    await expect(api_token_table_area.get_by_role("table")).to_be_visible()
    expected_table_name = "API Tokens"
    await expect(api_token_table_area.get_by_role("heading")).to_have_text(
        expected_table_name
    )


@pytest.mark.parametrize(
    "token_opt, note",
    [
        ("1 Hour", 'some note'),
        ("1 Day", False),
        ("1 Week", False),
        ("Never", 'some note'),
        # "server_up" token type is not from the list in the token request form:
        # when the server started it shows on the API Tokens table
        ("server_up", False),
    ],
)
async def test_request_token_expiration(app, browser, token_opt, note, user):
    """verify request token with the different options"""

    if token_opt == "server_up":
        # open the home page
        await open_home_page(app, browser, user)
        # start server via clicking on the Start button
        async with browser.expect_navigation(url=f"**/user/{user.name}/"):
            await browser.locator("#start").click()
        token_page = url_path_join(public_host(app), app.base_url, '/hub/token')
        await browser.goto(token_page)
    else:
        # open the token page
        await open_token_page(app, browser, user)
        if token_opt not in ["Never", "server_up"]:
            await browser.get_by_label('Token expires').select_option(token_opt)
        if note:
            note_field = browser.get_by_role("textbox").first
            await note_field.fill(note)
        # click on Request token button
        reqeust_btn = browser.locator('//div[@class="text-center"]').get_by_role(
            "button"
        )
        await reqeust_btn.click()
        await browser.reload(wait_until="load")
    # API Tokens table: verify that elements are displayed
    api_token_table_area = browser.locator('//div[@class="row"]').nth(2)
    await expect(api_token_table_area.get_by_role("table")).to_be_visible()
    await expect(api_token_table_area.locator("tr.token-row")).to_have_count(1)

    # getting values from DB to compare with values on UI
    assert len(user.api_tokens) == 1
    orm_token = user.api_tokens[-1]

    if token_opt == "server_up":
        expected_note = "Server at " + ujoin(app.base_url, f"/user/{user.name}/")
    elif note:
        expected_note = note
    else:
        expected_note = "Requested via token page"
    assert orm_token.note == expected_note
    note_on_page = (
        await api_token_table_area.locator("tr.token-row")
        .get_by_role("cell")
        .nth(0)
        .inner_text()
    )
    assert note_on_page == expected_note
    last_used_text = (
        await api_token_table_area.locator("tr.token-row")
        .get_by_role("cell")
        .nth(1)
        .inner_text()
    )
    expires_at_text = (
        await api_token_table_area.locator("tr.token-row")
        .get_by_role("cell")
        .nth(3)
        .inner_text()
    )
    assert last_used_text == "Never"

    if token_opt == "Never":
        assert orm_token.expires_at is None
        assert expires_at_text == "Never"
    elif token_opt == "1 Hour":
        assert expires_at_text == "in an hour"
    elif token_opt == "1 Day":
        assert expires_at_text == "in a day"
    elif token_opt == "1 Week":
        assert expires_at_text == "in 7 days"
    elif token_opt == "server_up":
        assert orm_token.expires_at is None
        assert expires_at_text == "Never"
    # verify that the button for revoke is presented
    revoke_btn = (
        api_token_table_area.locator("tr.token-row")
        .get_by_role("cell")
        .nth(4)
        .get_by_role("button")
    )
    await expect(revoke_btn).to_be_visible()
    await expect(revoke_btn).to_have_text("revoke")


@pytest.mark.parametrize(
    "token_type",
    [
        ("server_up"),
        ("request_by_user"),
        ("both"),
    ],
)
async def test_revoke_token(app, browser, token_type, user):
    """verify API Tokens table contant in case the server is started"""

    # open the home page
    await open_home_page(app, browser, user)
    if token_type == "server_up" or token_type == "both":
        # Start server via clicking on the Start button
        async with browser.expect_navigation(url=f"**/user/{user.name}/"):
            await browser.locator("#start").click()
    # open the token page
    next_url = url_path_join(public_host(app), app.base_url, '/hub/token')
    await browser.goto(next_url)
    await expect(browser).to_have_url(re.compile(".*/hub/token"))
    if token_type == "both" or token_type == "request_by_user":
        request_btn = browser.locator('//div[@class="text-center"]').get_by_role(
            "button"
        )
        await request_btn.click()
        await browser.reload(wait_until="load")

    revoke_btns = browser.get_by_role("button", name="revoke")

    # verify that the token revoked from UI and the database
    if token_type in {"server_up", "request_by_user"}:
        await expect(revoke_btns).to_have_count(1)
        await expect(revoke_btns).to_have_count(len(user.api_tokens))
        # click Revoke button
        await revoke_btns.click()
        await expect(browser.locator("tr.token-row")).to_have_count(0)
        await expect(revoke_btns).to_have_count(0)
        await expect(revoke_btns).to_have_count(len(user.api_tokens))

    if token_type == "both":
        # verify that both tokens are revoked from UI and the database
        revoke_btns = browser.get_by_role("button", name="revoke")
        await expect(revoke_btns).to_have_count(2)
        assert len(user.api_tokens) == 2
        for button in await browser.query_selector_all('.revoke-token-btn'):
            await button.click()
        await expect(revoke_btns).to_have_count(0)
        await expect(revoke_btns).to_have_count(len(user.api_tokens))


# MENU BAR


@pytest.mark.parametrize(
    "page, logged_in",
    [
        # the home page: verify if links work on the top bar
        ("/hub/home", True),
        # the token page: verify if links work on the top bar
        ("/hub/token", True),
        # "hub/not" = any url that is not existed: verify if links work on the top bar
        ("hub/not", True),
        # the login page: verify if links work on the top bar
        ("", False),
    ],
)
async def test_menu_bar(app, browser, page, logged_in, user):
    url = url_path_join(
        public_host(app),
        url_concat(
            url_path_join(app.base_url, "/login?next="),
            {"next": url_path_join(app.base_url, page)},
        ),
    )
    await browser.goto(url)
    if page:
        await login(browser, user.name, password=user.name)
    bar_link_elements = browser.locator('//div[@class="container-fluid"]//a')

    if not logged_in:
        await expect(bar_link_elements).to_have_count(1)
    elif "hub/not" in page:
        await expect(bar_link_elements).to_have_count(3)
    else:
        await expect(bar_link_elements).to_have_count(4)
        user_name = browser.get_by_text(f"{user.name}")
        await expect(user_name).to_be_visible()

    # verify the title on the logo
    logo = browser.get_by_role("img")
    await expect(logo).to_have_attribute("title", "Home")
    expected_link_bar_url = ["/hub/", "/hub/home", "/hub/token", "/hub/logout"]
    expected_link_bar_name = ["", "Home", "Token", "Logout"]
    for index in range(await bar_link_elements.count()):
        # verify that links on the topbar work, checking the titles of links
        link = bar_link_elements.nth(index)
        await expect(bar_link_elements.nth(index)).to_have_attribute(
            'href', re.compile('.*' + expected_link_bar_url[index])
        )
        await expect(bar_link_elements.nth(index)).to_have_text(
            expected_link_bar_name[index]
        )
        await link.click()
        if index == 0:
            if not logged_in:
                expected_url = f"hub/login?next={url_escape(app.base_url)}"
                assert expected_url in browser.url
            else:
                await expect(browser).to_have_url(re.compile(f".*/user/{user.name}/"))
                await browser.go_back()
                await expect(browser).to_have_url(re.compile(".*" + page))
        elif index == 3:
            await expect(browser).to_have_url(re.compile(".*/login"))
        else:
            await expect(browser).to_have_url(
                re.compile(".*" + expected_link_bar_url[index])
            )


# LOGOUT


@pytest.mark.parametrize(
    "url",
    [("/hub/home"), ("/hub/token"), ("/hub/spawn")],
)
async def test_user_logout(app, browser, url, user):
    if "/hub/home" in url:
        await open_home_page(app, browser, user)
    elif "/hub/token" in url:
        await open_home_page(app, browser, user)
    elif "/hub/spawn" in url:
        await open_spawn_pending(app, browser, user)
    logout_btn = browser.get_by_role("button", name="Logout")
    await expect(logout_btn).to_be_enabled()
    await logout_btn.click()
    # checking url changing to login url and login form is displayed
    await expect(browser).to_have_url(re.compile(".*/hub/login"))
    form = browser.locator('//*[@id="login-main"]/form')
    await expect(form).to_be_visible()
    bar_link_elements = browser.locator('//div[@class="container-fluid"]//a')
    await expect(bar_link_elements).to_have_count(1)
    await expect(bar_link_elements).to_have_attribute('href', (re.compile(".*/hub/")))

    # verify that user can login after logout
    await login(browser, user.name, password=user.name)
    await expect(browser).to_have_url(re.compile(".*/user/" + f"{user.name}/"))


# OAUTH confirmation page


@pytest.mark.parametrize(
    "user_scopes",
    [
        ([]),  # no scopes
        (  # user has just access to own resources
            [
                'self',
            ]
        ),
        (  # user has access to all groups resources
            [
                'read:groups',
                'groups',
            ]
        ),
        (  # user has access to specific users/groups/services resources
            [
                'read:users!user=gawain',
                'read:groups!group=mythos',
                'read:services!service=test',
            ]
        ),
    ],
)
async def test_oauth_page(
    app,
    browser,
    mockservice_url,
    create_temp_role,
    create_user_with_scopes,
    user_scopes,
):
    # create user with appropriate access permissions
    service_role = create_temp_role(user_scopes)
    service = mockservice_url
    user = create_user_with_scopes("access:services")
    roles.grant_role(app.db, user, service_role)
    oauth_client = (
        app.db.query(orm.OAuthClient)
        .filter_by(identifier=service.oauth_client_id)
        .one()
    )
    oauth_client.allowed_scopes = sorted(roles.roles_to_scopes([service_role]))
    app.db.commit()
    # open the service url in the browser
    service_url = url_path_join(public_url(app, service) + 'owhoami/?arg=x')
    await browser.goto(service_url)

    expected_redirect_url = app.base_url + f"services/{service.name}/oauth_callback"
    expected_client_id = f"client_id=service-{service.name}".replace("=", "%3D")

    # decode the URL
    decoded_browser_url = urllib.parse.unquote(urllib.parse.unquote(browser.url))

    # check if the client_id and redirected url in the browser_url
    assert expected_client_id in browser.url
    assert expected_redirect_url in decoded_browser_url

    # login user
    await login(browser, user.name, password=str(user.name))
    auth_btn = browser.locator('//input[@type="submit"]')
    await expect(auth_btn).to_be_enabled()
    text_permission = browser.get_by_role("paragraph")
    await expect(text_permission).to_contain_text(f"JupyterHub service {service.name}")
    await expect(text_permission).to_contain_text(f"oauth URL: {expected_redirect_url}")

    # verify that user can see the service name and oauth URL
    # permissions check
    oauth_form = browser.locator('//form')
    scopes_elements = await oauth_form.locator(
        '//input[@type="hidden" and @name="scopes"]'
    ).all()

    # checking that scopes are invisible on the page
    scope_list_oauth_page = [
        await expect(scopes_element).not_to_be_visible()
        for scopes_element in scopes_elements
    ]
    # checking that all scopes granded to user are presented in POST form (scope_list)
    scope_list_oauth_page = [
        await scopes_element.get_attribute("value")
        for scopes_element in scopes_elements
    ]
    assert all(x in scope_list_oauth_page for x in user_scopes)
    assert f"access:services!service={service.name}" in scope_list_oauth_page

    # checking that user cannot uncheck the checkbox
    check_boxes = await oauth_form.get_by_role('checkbox', name="raw-scopes").all()
    for check_box in check_boxes:
        await expect(check_box).not_to_be_editable()
        await expect(check_box).to_be_disabled()
        await expect(check_box).to_have_value("title", "This authorization is required")

    # checking that appropriete descriptions are displayed depending of scopes
    descriptions = await oauth_form.locator('//span').all()
    desc_list_form = [await description.text_content() for description in descriptions]
    desc_list_form = [" ".join(desc.split()) for desc in desc_list_form]

    # getting descriptions from scopes.py to compare them with descriptions on UI
    scope_descriptions = scopes.describe_raw_scopes(
        user_scopes or ['(no_scope)'], user.name
    )
    desc_list_expected = [
        f"{sd['description']} Applies to {sd['filter']}."
        if sd.get('filter')
        else sd['description']
        for sd in scope_descriptions
    ]
    assert sorted(desc_list_form) == sorted(desc_list_expected)

    # click on the Authorize button
    await auth_btn.click()
    # check that user returned to service page
    await expect(browser).to_have_url(service_url)
    # check the granted permissions by
    # getting the scopes from the service page,
    # which contains the JupyterHub user model
    text = await browser.locator("//body").text_content()
    user_model = json.loads(text)
    authorized_scopes = user_model["scopes"]
    # resolve the expected expanded scopes
    # authorized for the service
    expected_scopes = scopes.expand_scopes(user_scopes, owner=user.orm_user)
    expected_scopes |= scopes.access_scopes(oauth_client)
    expected_scopes |= scopes.identify_scopes(user.orm_user)

    # compare the scopes on the service page with the expected scope list
    assert sorted(authorized_scopes) == sorted(expected_scopes)


# ADMIN UI


async def open_admin_page(app, browser, user):
    """Login as `user` and open the admin page"""
    admin_page = url_escape(app.base_url) + "hub/admin"
    url = url_path_join(public_host(app), app.hub.base_url, "/login?next=" + admin_page)
    await browser.goto(url)
    await login(browser, user.name, password=str(user.name))
    await expect(browser).to_have_url(re.compile(".*/hub/admin"))


def create_list_of_users(create_user_with_scopes, n):
    return [create_user_with_scopes(["users"]) for i in range(1, n)]


async def test_start_stop_all_servers_on_admin_page(app, browser, admin_user):
    """verifying of working "Start All"/"Stop All" buttons"""

    await open_admin_page(app, browser, admin_user)
    # get total count of users from db
    users_count_db = app.db.query(orm.User).count()
    start_all_btn = browser.get_by_test_id("start-all")
    stop_all_btn = browser.get_by_test_id("stop-all")
    # verify Start All and Stop All buttons are displayed
    await expect(start_all_btn).to_be_enabled()
    await expect(stop_all_btn).to_be_enabled()

    users = browser.get_by_test_id("user-row-name")
    # verify that all servers are not started
    # users´numbers are the same as numbers of the start button and the Spawn page button
    # no Stop server buttons are displayed
    # no access buttons are displayed
    btns_start = browser.get_by_test_id("user-row-server-activity").get_by_role(
        "button", name="Start Server"
    )
    btns_stop = browser.get_by_test_id("user-row-server-activity").get_by_role(
        "button", name="Stop Server"
    )
    btns_spawn = browser.get_by_test_id("user-row-server-activity").get_by_role(
        "button", name="Spawn Page"
    )
    btns_access = browser.get_by_test_id("user-row-server-activity").get_by_role(
        "button", name="Access Server"
    )

    assert (
        await btns_start.count()
        == await btns_spawn.count()
        == await users.count()
        == users_count_db
    )
    assert await btns_stop.count() == await btns_access.count() == 0

    # start all servers via the Start All
    await start_all_btn.click()
    # Start All and Stop All are still displayed
    await expect(start_all_btn).to_be_enabled()
    await expect(stop_all_btn).to_be_enabled()

    for btn_start in await btns_start.all():
        await btn_start.wait_for(state="hidden")
    # users´numbers are the same as numbers of the stop button and the Access button
    # no Start server buttons are displayed
    # no Spawn page buttons are displayed
    assert await btns_start.count() == await btns_spawn.count() == 0
    assert (
        await btns_stop.count()
        == await btns_access.count()
        == await users.count()
        == users_count_db
    )
    # stop all servers via the Stop All
    await stop_all_btn.click()
    for btn_stop in await btns_stop.all():
        await btn_stop.wait_for(state="hidden")
    # verify that all servers are stopped
    # users´numbers are the same as numbers of the start button and the Spawn page button
    # no Stop server buttons are displayed
    # no access buttons are displayed
    await expect(start_all_btn).to_be_enabled()
    await expect(stop_all_btn).to_be_enabled()
    assert (
        await btns_start.count()
        == await btns_spawn.count()
        == await users.count()
        == users_count_db
    )


@pytest.mark.parametrize("added_count_users", [10, 47, 48, 49, 110])
async def test_paging_on_admin_page(
    app, browser, admin_user, added_count_users, create_user_with_scopes
):
    """verifying of displaying number of total users on the admin page and navigation with "Previous"/"Next" buttons"""

    create_list_of_users(create_user_with_scopes, added_count_users)
    await open_admin_page(app, browser, admin_user)
    # get total count of users from db
    users_count_db = app.db.query(orm.User).count()
    # get total count of users from UI page
    displaying = browser.get_by_text("Displaying")
    btn_previous = browser.get_by_role("button", name="Previous")
    btn_next = browser.get_by_role("button", name="Next")
    # verify "Previous"/"Next" button clickability depending on users number on the page
    await expect(displaying).to_have_text(
        re.compile(".*" + f"0-{min(users_count_db,50)}" + ".*")
    )
    if users_count_db > 50:
        await expect(btn_next.locator("//span")).to_have_class("active-pagination")
        # click on Next button
        await btn_next.click()
        if users_count_db <= 100:
            await expect(displaying).to_have_text(
                re.compile(".*" + f"50-{users_count_db}" + ".*")
            )
        else:
            await expect(displaying).to_have_text(re.compile(".*" + "50-100" + ".*"))
            await expect(btn_next.locator("//span")).to_have_class("active-pagination")
        await expect(btn_previous.locator("//span")).to_have_class("active-pagination")
        # click on Previous button
        await btn_previous.click()
    else:
        await expect(btn_next.locator("//span")).to_have_class("inactive-pagination")
        await expect(btn_previous.locator("//span")).to_have_class(
            "inactive-pagination"
        )


# TODO: find out how to replace await asyncio.sleep(1)
@pytest.mark.parametrize(
    "added_count_users, search_value",
    [
        # the value of search is absent =>the expected result null records are found
        (10, "not exists"),
        # a search value is a middle part of users name (number,symbol,letter)
        (25, "r_5"),
        # a search value equals to number
        (50, "1"),
        # searching result shows on more than one page
        (60, "user"),
    ],
)
async def test_search_on_admin_page(
    app,
    browser,
    admin_user,
    create_user_with_scopes,
    added_count_users,
    search_value,
):

    create_list_of_users(create_user_with_scopes, added_count_users)
    await open_admin_page(app, browser, admin_user)
    element_search = browser.locator('//input[@name="user_search"]')
    await element_search.click()
    await element_search.fill(search_value, force=True)
    # TODO: find out how to replace sleep
    await asyncio.sleep(1)
    await browser.wait_for_load_state("networkidle")
    # get the result of the search from db
    users_count_db_filtered = (
        app.db.query(orm.User).filter(orm.User.name.like(f'%{search_value}%')).count()
    )
    # get the result of the search
    filtered_list_on_page = browser.locator('//tr[@class="user-row"]')

    # check that count of users matches with number of users on the footer
    displaying = browser.get_by_text("Displaying")

    # check that users names contain the search value in the filtered list
    for element in await filtered_list_on_page.get_by_test_id("user-row-name").all():
        await expect(element).to_contain_text(re.compile(f".*{search_value}.*"))
    if users_count_db_filtered <= 50:
        await expect(displaying).to_contain_text(
            re.compile(f"0-{users_count_db_filtered}")
        )
        await expect(filtered_list_on_page).to_have_count(users_count_db_filtered)
    else:
        await expect(displaying).to_contain_text(re.compile("0-50"))
        await expect(filtered_list_on_page).to_have_count(50)
        # click on Next button to verify that the rest part of filtered list is displayed on the next page
        await browser.get_by_role("button", name="Next").click()
        filtered_list_on_next_page = browser.locator('//tr[@class="user-row"]')
        await expect(filtered_list_on_page).to_have_count(users_count_db_filtered - 50)
        for element in await filtered_list_on_next_page.get_by_test_id(
            "user-row-name"
        ).all():
            await expect(element).to_contain_text(re.compile(f".*{search_value}.*"))


@pytest.mark.parametrize("added_count_users,index_user_1, index_user_2", [(5, 1, 0)])
async def test_start_stop_server_on_admin_page(
    app,
    browser,
    admin_user,
    create_user_with_scopes,
    added_count_users,
    index_user_1,
    index_user_2,
):
    async def start_user(browser, expected_user, index):
        """start the server for one user via the Start Server button, index = 0 or 1"""
        user = expected_user[index]
        start_btn_xpath = f'//a[contains(@href, "spawn/{user}")]/preceding-sibling::button[contains(@class, "start-button")]'
        start_btn = browser.locator(start_btn_xpath)
        await expect(start_btn).to_be_enabled()
        await start_btn.click()

    async def spawn_user(browser, expected_user, index):
        """spawn the server for one user via the Spawn page button, index = 0 or 1"""
        user = expected_user[index]
        spawn_btn_xpath = f'//a[contains(@href, "spawn/{user}")]/button[contains(@class, "secondary")]'
        spawn_btn = browser.locator(spawn_btn_xpath)
        await expect(spawn_btn).to_be_enabled()
        async with browser.expect_navigation(url=f"**/user/{user}/"):
            await spawn_btn.click()

    async def access_srv_user(browser, expected_user):
        """access to the server for users via the Access Server button"""
        for user in expected_user:
            access_btn_xpath = f'//a[contains(@href, "user/{user}")]/button[contains(@class, "primary")]'
            access_btn = browser.locator(access_btn_xpath)
            await expect(access_btn).to_be_enabled()
            await access_btn.click()
            await browser.go_back()

    async def stop_srv_users(browser, expected_user):
        """stop the server for one user via the Stop Server button"""
        for user in expected_user:
            stop_btn_xpath = f'//a[contains(@href, "user/{user}")]/preceding-sibling::button[contains(@class, "stop-button")]'
            stop_btn = browser.locator(stop_btn_xpath)
            await expect(stop_btn).to_be_enabled()
            await stop_btn.click()

    create_list_of_users(create_user_with_scopes, added_count_users)
    await open_admin_page(app, browser, admin_user)
    users = await browser.locator('//td[@data-testid="user-row-name"]').all()
    users_list = [await user.text_content() for user in users]
    users_list = [user.strip() for user in users_list]
    expected_user = [users_list[index_user_1], users_list[index_user_2]]

    # check that all users have correct link for Spawn Page
    spawn_page_btns = browser.locator(
        '//*[@data-testid="user-row-server-activity"]//a[contains(@href, "spawn/")]'
    )
    spawn_page_btns_list = await spawn_page_btns.all()
    for user, spawn_page_btn in zip(users, spawn_page_btns_list):
        user_from_table = await user.text_content()
        user_from_table = user_from_table.strip()
        link = await spawn_page_btn.get_attribute('href')
        assert f"/spawn/{user_from_table}" in link

    # click on Start button
    await start_user(browser, expected_user, index=0)
    await expect(browser.get_by_role("button", name="Stop Server")).to_have_count(1)
    await expect(browser.get_by_role("button", name="Start Server")).to_have_count(
        len(users_list) - 1
    )
    await expect(browser.get_by_role("button", name="Spawn Page")).to_have_count(
        len(users_list) - 1
    )

    # click on Spawn page button
    await spawn_user(browser, expected_user, index=1)
    await expect(browser).to_have_url(re.compile(".*" + f"/user/{expected_user[1]}/"))

    # open/return to the Admin page
    admin_page = url_path_join(public_host(app), app.hub.base_url, "admin")
    await browser.goto(admin_page)
    await expect(browser.get_by_role("button", name="Stop Server")).to_have_count(2)
    await expect(browser.get_by_role("button", name="Access Server")).to_have_count(2)
    await expect(browser.get_by_role("button", name="Start Server")).to_have_count(
        len(users_list) - 2
    )

    # click on the Access button
    await access_srv_user(browser, expected_user)
    await expect(browser.get_by_role("button", name="Stop Server")).to_have_count(2)
    await expect(browser.get_by_role("button", name="Start Server")).to_have_count(
        len(users_list) - 2
    )

    # click on Stop button for both users
    await stop_srv_users(browser, expected_user)
    await expect(browser.get_by_role("button", name="Stop Server")).to_have_count(0)
    await expect(browser.get_by_role("button", name="Access Server")).to_have_count(0)
    await expect(browser.get_by_role("button", name="Start Server")).to_have_count(
        len(users_list)
    )
    await expect(browser.get_by_role("button", name="Spawn Page")).to_have_count(
        len(users_list)
    )
