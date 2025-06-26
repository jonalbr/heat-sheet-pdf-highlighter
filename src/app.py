"""
Main application entry point and coordination
"""
from tkinter import Tk
from .gui.main_window import PDFHighlighterApp


def main():
    """Main application entry point."""
    root = Tk()
    PDFHighlighterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
