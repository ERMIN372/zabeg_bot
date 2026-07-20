from aiogram import Router

from bot.handlers.admin import edit, panel

admin_router = Router(name="admin_root")
admin_router.include_routers(panel.router, edit.router)
