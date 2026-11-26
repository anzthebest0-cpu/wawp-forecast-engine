from titan_panel_retention_policy import eligible,assert_inventory
old={"gfs","aifs-old","ifs","icon"};new={"gfs","aifs-new","ifs","icon"};third={"gfs-new","aifs-new","ifs","icon"}
assert eligible([old,new])==set()
assert eligible([old,new,third])=={"aifs-old"}
assert eligible([old,new,third],["aifs-old"])==set()
assert assert_inventory([old,new,third],old|new|third)=={"aifs-old"}
assert assert_inventory([old,new,third],(old|new|third)-{"aifs-old"},already_retired=["aifs-old"])==set()
for active,retired in [((old|new|third)|{"unknown"},[]),((old|new|third)-{"aifs-old"},[]),((old|new|third)-{"ifs"},["ifs"])]:
    try:assert_inventory([old,new,third],active,already_retired=retired)
    except ValueError:pass
    else:raise AssertionError("Unsafe retirement allowed")
print("PANEL_RETENTION_POLICY_TESTS_PASS")
