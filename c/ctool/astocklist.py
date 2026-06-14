import akshare as ak
import os
import time


def get_stock_list(retries=3):
    """
    Fetch A-share stock list with retry logic.
    """
    for i in range(retries):
        try:
            print(f"Fetching stock list... attempt {i + 1}")

            df = ak.stock_info_a_code_name()

            # standardize columns
            df.columns = ["symbol", "name"]

            print("Fetch successful")
            return df

        except Exception as e:
            print(f"Attempt {i + 1} failed: {e}")
            time.sleep(2)

    raise RuntimeError("Failed to fetch stock list after multiple retries")


def save_csv(df, filename="all_a_stocks.csv"):
    """
    Save CSV in the same directory as this script.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(script_dir, filename)

    df.to_csv(file_path, index=False)
    print(f"Saved CSV to: {file_path}")


def main():
    df = get_stock_list()
    save_csv(df)

    print("\nPreview:")
    print(df.head())


if __name__ == "__main__":
    main()
