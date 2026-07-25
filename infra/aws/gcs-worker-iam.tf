resource "google_storage_bucket_iam_member" "gpu_worker_read" {
  for_each = local.gcs_worker_read_prefixes

  bucket = each.value.bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${local.gpu_worker_service_account_email}"

  condition {
    title       = "WifRead-${substr(sha1(each.key), 0, 12)}"
    description = "Read only the approved watermark worker object prefix."
    expression = format(
      "resource.name.startsWith('projects/_/buckets/%s/objects/%s')",
      each.value.bucket,
      each.value.prefix,
    )
  }
}

resource "google_storage_bucket_iam_member" "gpu_worker_create" {
  for_each = local.gcs_worker_output_prefixes

  bucket = each.value.bucket
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${local.gpu_worker_service_account_email}"

  condition {
    title       = "WifCreate-${substr(sha1(each.key), 0, 12)}"
    description = "Create only under the approved watermark worker output prefix."
    expression = format(
      "resource.name.startsWith('projects/_/buckets/%s/objects/%s')",
      each.value.bucket,
      each.value.prefix,
    )
  }
}
