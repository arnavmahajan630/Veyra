import type { ContractState } from "../../api/types";
import { StatusDot, type StatusTone } from "../../components/StatusDot";
import { useI18n } from "../../i18n/i18n";

const TONE: Record<ContractState, StatusTone> = {
  draft: "idle",
  testing: "idle",
  canary: "warn",
  active: "good",
  retired: "idle",
};

/** A contract version's lifecycle state as a dot plus words (colour never carries meaning alone). */
export function ContractStateDot({ state }: { state: ContractState }) {
  const { t } = useI18n();
  return <StatusDot tone={TONE[state]} label={t(`contracts.state.${state}`)} />;
}
