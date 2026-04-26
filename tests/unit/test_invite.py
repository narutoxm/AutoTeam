from autoteam import invite


class _FakeLocator:
    def __init__(self, *, visible=False, text="", items=None):
        self._visible = visible
        self._text = text
        self._items = items

    @property
    def first(self):
        if self._items:
            return self._items[0]
        return self

    def is_visible(self, timeout=None):
        return self._visible

    def all(self):
        return self._items or []

    def inner_text(self, timeout=None):
        return self._text


class _FakePage:
    def __init__(self, url, locator_map=None):
        self.url = url
        self._locator_map = locator_map or {}

    def locator(self, selector):
        return self._locator_map.get(selector, _FakeLocator())


def test_detect_invite_register_step_prefers_about_you_over_code_inputs():
    page = _FakePage(
        "https://auth.openai.com/about-you",
        {
            "body": _FakeLocator(text="填写生日信息"),
            'input[maxlength="1"]': _FakeLocator(items=[_FakeLocator(visible=True) for _ in range(6)]),
        },
    )

    assert invite._detect_invite_register_step(page) == "profile"


def test_wait_for_code_submit_result_accepts_about_you_transition():
    page = _FakePage(
        "https://auth.openai.com/about-you",
        {
            "body": _FakeLocator(text="填写生日信息"),
            'input[maxlength="1"]': _FakeLocator(items=[_FakeLocator(visible=True) for _ in range(6)]),
        },
    )

    assert invite._wait_for_code_submit_result(page, timeout=1) == ("accepted", "")
