from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👀 WATCH"), KeyboardButton(text="📊 STATUS")],
            [KeyboardButton(text="🚨 RECENT"), KeyboardButton(text="📈 STATS")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Оберіть звіт",
    )
