/* SPDX-License-Identifier: GPL-2.0-or-later
 * Linux/glibc-only lab diagnostics, not part of the compressed-store design.
 */
#include <malloc.h>

static char *ct3_allocator_stats(Object *obj, Error **errp)
{
    struct mallinfo2 m = mallinfo2();
    return g_strdup_printf("arena=%zu used=%zu free=%zu mapped=%zu releasable_top=%zu",
                           m.arena, m.uordblks, m.fordblks, m.hblkhd, m.keepcost);
}

static void ct3_allocator_trim(Object *obj, bool value, Error **errp)
{
    if (value) {
        malloc_trim(0);
    }
}

static void ct3_allocator_diagnostics_init(Object *obj)
{
    object_property_add_str(obj, "lab-allocator-stats", ct3_allocator_stats, NULL);
    object_property_add_bool(obj, "lab-allocator-trim", NULL, ct3_allocator_trim);
}
