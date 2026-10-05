import { usePreventRemove, useNavigation } from "@react-navigation/native";
import { useAuth } from "@/providers/AuthProvider";
import { useCopy } from "./copy";
import { confirm } from "./feedback";
export function useDirtyGuard(dirty: boolean) {
  const navigation = useNavigation();
  const c = useCopy();
  const { state } = useAuth();
  usePreventRemove(dirty && state.status === "signed-in", ({ data }) => {
    void confirm(
      c("离开编辑？", "Leave the editor?"),
      c("未保存的更改将被丢弃。", "Unsaved changes will be discarded."),
      c("离开", "Leave"),
      c("继续编辑", "Keep editing"),
    ).then((ok) => {
      if (ok) navigation.dispatch(data.action);
    });
  });
}
