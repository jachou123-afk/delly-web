CREATE TABLE `ad_selection_batches` (
  `id` text PRIMARY KEY NOT NULL,
  `created_at` text NOT NULL,
  `status` text DEFAULT 'pending' NOT NULL CHECK (`status` IN ('pending', 'reviewed')),
  `request_json` text NOT NULL,
  `products_json` text NOT NULL,
  `reviewed_at` text,
  `recommended_product_ids_json` text
);
--> statement-breakpoint
CREATE INDEX `ad_selection_batches_status_created` ON `ad_selection_batches` (`status`, `created_at`, `id`);
