import sys
import subprocess


def main():
    print(
        "update_version.py is deprecated. Use: python create_release.py <version> (creates signed tag and pushes)."
    )
    if len(sys.argv) < 2:

        version = input("Enter the new version (x.y.z or x.y.z-rcN/-betaN): ")
    else:
        version = sys.argv[1]

    # Delegate to create_release.py to bump files, sign tag, and push.
    cmd = [sys.executable, "create_release.py", version]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
