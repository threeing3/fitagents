export function goalLabel(value: unknown, isZh: boolean): string {
  const labels: Record<string, string> = { maintenance: "维持状态", fat_loss: "减脂", muscle_gain: "增肌", endurance: "提升耐力", strength: "提升力量", general_fitness: "保持健康与体能" };
  if (value == null || value === "") return isZh ? "目标尚未设置" : "No goal set";
  const text = String(value);
  return isZh ? labels[text] || text : text;
}
