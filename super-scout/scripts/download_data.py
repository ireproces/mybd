from pathlib import Path
import shutil
import zipfile

import kagglehub


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
ARCHIVE = RAW / "european-soccer-database.zip"
DATASET_REF = "hugomathien/soccer"


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    if not ARCHIVE.exists() and not list(RAW.glob("*.sqlite")):
        print("Downloading European Soccer Database from Kaggle...")
        downloaded = Path(kagglehub.dataset_download(DATASET_REF))
        archive = next(downloaded.glob("*.zip"), None)
        if archive:
            shutil.copy2(archive, ARCHIVE)
        else:
            for source in downloaded.iterdir():
                destination = RAW / source.name
                if source.is_file():
                    shutil.copy2(source, destination)
    if ARCHIVE.exists():
        with zipfile.ZipFile(ARCHIVE) as archive:
            archive.extractall(RAW)
    sqlite_files = list(RAW.glob("*.sqlite"))
    if not sqlite_files:
        raise FileNotFoundError("Kaggle archive did not contain a SQLite database")
    print(f"Dataset ready: {sqlite_files[0]}")


if __name__ == "__main__":
    main()
