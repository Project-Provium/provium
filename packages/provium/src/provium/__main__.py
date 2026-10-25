def main() -> int:
    """Run Provium's bundled command-line interface."""
    from provium.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
