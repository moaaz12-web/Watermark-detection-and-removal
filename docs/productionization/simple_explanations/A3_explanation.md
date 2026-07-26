The complete A3 description is:
We created a set of authenticated internal APIs and deployed them using AWS API Gateway. These APIs allow the GCP backend to submit a GPU job, check its status, or request cancellation.
Before calling the API, the approved GCP orchestration service account obtains a Google identity token. GCP includes this token in the request:
Authorization: Bearer GOOGLE_ID_TOKEN
API Gateway checks:
Google issued the token
The token’s signature is valid
The token has not expired
The token was created specifically for our AWS GPU API
If the token is invalid, API Gateway rejects the request before our code runs.
If the token is valid, API Gateway forwards the request to the submission Lambda.
The Lambda then performs more specific checks:
The caller is the exact approved GCP service account
The request has the correct fields
The job ID in the URL matches the body
The GCS manifest and output locations are approved
The pipeline version is allowed
The manifest generation is valid
The request hash is correct
The caller is not trying to override the GPU, image, command, role, timeout, or other infrastructure settings
After validation, Lambda checks DynamoDB.
DynamoDB acts as the AWS submission ledger. It answers:
“Have we already processed this application job?”

If the same job and request were previously submitted, Lambda returns the existing result instead of creating another GPU job.
If the same job ID is reused with different contents, Lambda rejects it as a conflict.
If it is a genuinely new request, Lambda reserves the job in DynamoDB and then submits it to the fixed AWS Batch queue using the fixed Batch job definition.
AWS Batch will eventually:
Start the approved g4dn.xlarge
Run the exact container image
Pass it the approved manifest information
Let the worker process the images
Shut the machine down afterward
Lambda stores the AWS Batch job ID in DynamoDB and returns it to GCP.
For status requests, Lambda checks the DynamoDB record and AWS Batch, then returns a normalized status to GCP.
For cancellation requests, Lambda confirms the job belongs to the requested application job and asks AWS Batch to terminate it.
So, in simple words:
A3 created a secure AWS API that only our approved GCP service account can call. API Gateway validates the Google identity token, Lambda validates the caller and job contents, DynamoDB prevents duplicate submissions, and Lambda communicates with AWS Batch through a tightly restricted interface.

Currently, the final submission step is deliberately disabled:
GCP
  → API Gateway authenticates
  → Lambda validates
  → DynamoDB provides idempotency
  → AWS Batch submission blocked by safety switches
The API and its supporting services are deployed, but it cannot start a GPU until we intentionally enable the Batch queue and submission switch in A7.