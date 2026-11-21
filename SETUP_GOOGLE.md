# Google Drive & Sheets Setup Guide for Jobee

Jobee runs **100% locally** by default. Setting up Google Drive and Google Sheets is **completely optional**.
If you want automatic resume link syncing and Google Sheets tracking, follow these steps:

---

## 1. Create a Google Cloud Project
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g., `Jobee-Tracker`).

## 2. Enable APIs
In your project, enable the following two APIs:
- **Google Drive API**
- **Google Sheets API**

## 3. Configure OAuth Consent Screen
1. Go to **APIs & Services > OAuth consent screen**.
2. Select **External** (or Internal if using Google Workspace) and click **Create**.
3. Fill in the App Name (`Jobee`) and your email.
4. Under **Scopes**, add:
   - `https://www.googleapis.com/auth/drive.file`
   - `https://www.googleapis.com/auth/spreadsheets`
5. Under **Test users**, add your own Google email address.

## 4. Download `credentials.json`
1. Go to **APIs & Services > Credentials**.
2. Click **Create Credentials > OAuth client ID**.
3. Application type: **Desktop app**.
4. Name: `Jobee Local Client`.
5. Click **Create**, then click **Download JSON**.
6. Rename the downloaded file to `credentials.json` and place it in the root of the project:
   `d:\vishnu personal\jobee\credentials.json`

## 5. Configure `config.yaml`
1. Create a blank Google Sheet in your Google Drive.
2. Copy the Spreadsheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit`
3. (Optional) Create a Google Drive folder for resumes and copy its Folder ID from the URL:
   `https://drive.google.com/drive/folders/<FOLDER_ID>`
4. Update `config.yaml` or use the **Resume & Integrations** tab in the Jobee Web UI:
   ```yaml
   google_sheet_id: "your_spreadsheet_id_here"
   google_drive_folder_id: "your_folder_id_here"
   ```

## 6. Authorize on First Sync
When you trigger "Sync to Google Sheets" or start Jobee with Google configured, a browser window will open asking you to sign in and grant permission.
Once granted, a `token.json` file will be created locally in the root directory for future automatic access.
