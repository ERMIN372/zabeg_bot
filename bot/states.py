from aiogram.fsm.state import State, StatesGroup


class ProfileForm(StatesGroup):
    name = State()
    phone = State()
    email = State()
    marketing = State()


class SupportChat(StatesGroup):
    chatting = State()


class AdminCreateEvent(StatesGroup):
    title = State()
    description = State()
    date = State()
    time = State()
    tz = State()
    image = State()
    kind = State()
    loc_name = State()
    loc_address = State()
    loc_capacity = State()


class AdminEditField(StatesGroup):
    value = State()


class AdminEditContent(StatesGroup):
    text = State()


class AdminMoveEvent(StatesGroup):
    date = State()
    time = State()
