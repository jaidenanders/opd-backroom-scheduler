import logging
import sys
import traceback
from pathlib import Path


def _setup_logging():
    log_dir = Path(__file__).parent.parent
    log_file = log_dir / "backroom_scheduler.log"
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
            ),
            logging.StreamHandler(sys.stderr),
        ],
    )


def main():
    import logging.handlers
    _setup_logging()

    try:
        from .app import BackroomApp
        BackroomApp().mainloop()
    except Exception:
        msg = traceback.format_exc()
        logging.getLogger(__name__).critical("Unhandled exception:\n%s", msg)
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "Unexpected Error",
                "The scheduler encountered an unexpected error and must close.\n\n"
                f"{msg}\n\nDetails saved to backroom_scheduler.log",
            )
            root.destroy()
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
