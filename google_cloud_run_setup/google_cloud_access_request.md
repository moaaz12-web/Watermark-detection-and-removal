# Google Cloud Access Request

Hi, I need additional Google Cloud access so I can finish deploying the backend API to Cloud Run and fix deployment issues without needing you to make every permission change manually.

The backend deployment needs access to Cloud Run, Cloud Build, Cloud SQL, Secret Manager, Artifact Registry, logs, and service account permissions.

Project:

```text
project-5909a738-2fdc-451e-ad6
```

Developer account:

```text
moaaz7151@gmail.com
```

Google Cloud IAM page:

```text
https://console.cloud.google.com/iam-admin/iam?project=project-5909a738-2fdc-451e-ad6
```

Please choose one of the two options below.

## Option 1: Easiest Option

Grant this role to `moaaz7151@gmail.com`:

```text
Owner
```

This is the easiest option while the backend is being set up. After deployment is stable, this access can be reduced.

### Steps

1. Open this link:

```text
https://console.cloud.google.com/iam-admin/iam?project=project-5909a738-2fdc-451e-ad6
```

2. Find this user in the table:

```text
moaaz7151@gmail.com
```

3. If the user is already there, click the pencil/edit icon on that row.

4. Click:

```text
Add another role
```

5. Select this role:

```text
Owner
```

6. Click:

```text
Save
```

7. If the user is not already in the table, click:

```text
Grant access
```

8. Enter this email:

```text
moaaz7151@gmail.com
```

9. Select this role:

```text
Owner
```

10. Click:

```text
Save
```

## Option 2: More Restricted Option

If you do not want to grant `Owner`, grant these roles instead:

```text
Viewer
Project IAM Admin
Service Account Admin
Service Account User
Cloud Run Admin
Cloud Run Source Developer
Cloud Build Editor
Cloud Build Connection Admin
Cloud Build Approver
Secret Manager Admin
Artifact Registry Administrator
Cloud SQL Admin
Service Usage Admin
Logging Admin
```

This is a longer list because Google Cloud separates deployment, build triggers, GitHub/source connections, logging, service accounts, and database permissions.

### Steps

1. Open this link:

```text
https://console.cloud.google.com/iam-admin/iam?project=project-5909a738-2fdc-451e-ad6
```

2. Find this user in the table:

```text
moaaz7151@gmail.com
```

3. If the user is already there, click the pencil/edit icon on that row.

4. Click:

```text
Add another role
```

5. Add each role from the list above.

6. Click:

```text
Save
```

7. If the user is not already in the table, click:

```text
Grant access
```

8. Enter this email:

```text
moaaz7151@gmail.com
```

9. Add each role from the list above.

10. Click:

```text
Save
```

If Google Cloud does not allow adding all roles at once, add one role, save, then edit the same user again and add the next role.

If billing setup is blocked later, billing access may still need to be granted separately by the billing account owner. That is separate from project access.

## Confirm

After saving, please tell me it is done. I will refresh Google Cloud Console and continue the deployment.
