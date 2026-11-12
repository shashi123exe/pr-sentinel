"""Unified diff patch fixtures covering various git change scenarios."""

# Multiple hunks in a single file
PATCH_MULTIPLE_HUNKS = """@@ -1,5 +1,6 @@
 def hello():
-    return 'old'
+    msg = 'new'
+    return msg

 def middle():
@@ -10,4 +11,5 @@
 def farewell():
-    return 'bye'
+    farewell_msg = 'goodbye'
+    return farewell_msg
"""

# Added file (new lines only, old_start=0)
PATCH_ADDED_FILE = """@@ -0,0 +1,4 @@
+import os
+
+def get_user():
+    return os.getenv("USER")
"""

# Deleted file (removed lines only, new_start=0)
PATCH_DELETED_FILE = """@@ -1,3 +0,0 @@
-def deprecated():
-    pass
-
"""

# Renamed file with small change
PATCH_RENAMED_FILE = """@@ -1,3 +1,3 @@
 # Module config
-VERSION = "1.0.0"
+VERSION = "1.0.1"
"""

# Patch with "\\ No newline at end of file" marker
PATCH_NO_NEWLINE = r"""@@ -1,2 +1,3 @@
 line 1
-line 2
\ No newline at end of file
+line 2 modified
+line 3 added
\ No newline at end of file
"""

# Malformed patch (corrupted headers and garbage lines)
PATCH_MALFORMED = """@@ invalid header @@
garbage text line without plus or minus
@@ -1,2 +1,2 @@
 valid context
+valid added line
"""
