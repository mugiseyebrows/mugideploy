try:
    from . import main
except ImportError:
    from __init__ import main

if __name__ == "__main__":
    main()