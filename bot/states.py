from aiogram.fsm.state import State, StatesGroup


class StaffStates(StatesGroup):
    searching = State()
    selecting_item = State()
    entering_qty = State()
    editing_qty = State()
    selecting_storage = State()
    selecting_reason = State()
    entering_comment = State()


class AdminStates(StatesGroup):
    entering_reject_reason = State()
    entering_staff_name = State()
    entering_reason_name = State()
    entering_reason_poster_id = State()


class OnboardingStates(StatesGroup):
    entering_poster_account = State()
    entering_invite_code = State()
