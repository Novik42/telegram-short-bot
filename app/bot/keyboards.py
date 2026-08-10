from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👀 WATCH"), KeyboardButton(text="📊 STATUS")],
            [KeyboardButton(text="🚨 RECENT"), KeyboardButton(text="📈 STATS")],
            [KeyboardButton(text="🧪 DEMO")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Оберіть звіт",
    )


def prepare_short_keyboard(transition_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📉 Підготувати DEMO SHORT",
                    callback_data=f"trade:prepare:{transition_id}",
                )
            ]
        ]
    )


def confirm_short_keyboard(trade_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Підтвердити вхід",
                    callback_data=f"trade:confirm:{trade_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Скасувати",
                    callback_data=f"trade:cancel:{trade_id}",
                ),
            ]
        ]
    )


def close_trade_keyboard(trade_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛑 Закрити DEMO позицію",
                    callback_data=f"trade:close:{trade_id}",
                )
            ]
        ]
    )
