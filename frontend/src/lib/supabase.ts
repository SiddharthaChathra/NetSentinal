import { createClient } from '@supabase/supabase-js';

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || 'https://awsnemrstsjsydwcjfcw.supabase.co';
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImF3c25lbXJzdHNqc3lkd2NqZmN3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODY3NzE1MDksImV4cCI6MjEwMjM0NzUwOX0.yvA3ElVqSa-jlrZm_UpOT_pKiKG7dTCAHmJxPm9Nnx0';

export const supabase = createClient(supabaseUrl, supabaseAnonKey);
